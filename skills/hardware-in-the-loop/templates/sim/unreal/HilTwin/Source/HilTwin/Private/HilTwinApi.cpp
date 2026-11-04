/**
 * @file HilTwinApi.cpp
 * @brief Private copy, load, bind-by-name and ABI check of the twin library.
 */
#include "HilTwinApi.h"

#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "HilTwinModule.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "hil_twin/hil_twin_api.h"

bool FHilTwinApi::Load(const FString& SourcePath, FString& OutError)
{
	OutError.Reset();
	// ---- one table, one load: a second Load on a live table is a programming error, refused loudly
	if (Module)
	{
		OutError = TEXT("twin library already loaded in this table");
		return false;
	}

	// ---- resolve the source: absolute as given, relative against the project directory
	FString Src = SourcePath;
	if (FPaths::IsRelative(Src))
	{
		Src = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir(), Src);
	}
	if (Src.IsEmpty() || !IFileManager::Get().FileExists(*Src))
	{
		OutError = FString::Printf(TEXT("twin library '%s' does not exist (build sim/twin with CMake first)"), *Src);
		return false;
	}

	// ---- private copy: <Saved>/HilTwin/<guid>/<file>
	CopyDir = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("HilTwin"), FGuid::NewGuid().ToString(EGuidFormats::Digits));
	const FString Dst = FPaths::Combine(CopyDir, FPaths::GetCleanFilename(Src));
	if (!IFileManager::Get().MakeDirectory(*CopyDir, true) || IFileManager::Get().Copy(*Dst, *Src) != COPY_OK)
	{
		OutError = FString::Printf(TEXT("cannot copy the twin library to '%s'"), *Dst);
		return false;
	}

	Module = FPlatformProcess::GetDllHandle(*Dst);
	if (!Module)
	{
		OutError = FString::Printf(TEXT("loading '%s' failed (missing runtime dependency or wrong architecture)"), *Dst);
		return false;
	}

	// ---- bind by name; collect every missing export so one message names them all
	TArray<FString> Missing;
	auto Bind = [this, &Missing](auto& Fn, const TCHAR* Name)
	{
		void* P = FPlatformProcess::GetDllExport(Module, Name);
		Fn = reinterpret_cast<std::remove_reference_t<decltype(Fn)>>(P);
		if (!P)
		{
			Missing.Add(Name);
		}
	};
	Bind(AbiVersion, TEXT("hil_twin_abi_version"));
	Bind(LastErrorFn, TEXT("hil_twin_last_error"));
	Bind(Create, TEXT("hil_twin_create"));
	Bind(Destroy, TEXT("hil_twin_destroy"));
	Bind(Dt, TEXT("hil_twin_dt"));
	Bind(Time, TEXT("hil_twin_time"));
	Bind(Step, TEXT("hil_twin_step"));
	Bind(StepExternal, TEXT("hil_twin_step_external"));
	Bind(JointCount, TEXT("hil_twin_joint_count"));
	Bind(GetJoint, TEXT("hil_twin_get_joint"));
	Bind(JointProps, TEXT("hil_twin_joint_props"));
	Bind(LinkTx, TEXT("hil_twin_link_tx"));
	Bind(LinkRx, TEXT("hil_twin_link_rx"));
	Bind(SendCommand, TEXT("hil_twin_send_command"));
	Bind(GetScalar, TEXT("hil_twin_get_scalar"));
	Bind(SetScalar, TEXT("hil_twin_set_scalar"));
	Bind(TraceHash, TEXT("hil_twin_trace_hash"));
	if (Missing.Num() > 0)
	{
		OutError = FString::Printf(TEXT("twin library lacks exports: %s (stale build?)"), *FString::Join(Missing, TEXT(", ")));
		Unload();
		return false;
	}

	// ---- the ABI must be the one this plugin was compiled against
	const int Abi = AbiVersion();
	if (Abi != HIL_TWIN_ABI_VERSION)
	{
		OutError = FString::Printf(TEXT("twin ABI %d, plugin built for %d: rebuild one of them"), Abi, HIL_TWIN_ABI_VERSION);
		Unload();
		return false;
	}
	UE_LOG(LogHilTwin, Log, TEXT("twin library loaded from %s (ABI %d)"), *Dst, Abi);
	return true;
}

void FHilTwinApi::Unload()
{
	if (Module)
	{
		FPlatformProcess::FreeDllHandle(Module);
		Module = nullptr;
	}
	// ---- the private copy is ours alone: delete it by its exact folder
	if (!CopyDir.IsEmpty())
	{
		IFileManager::Get().DeleteDirectory(*CopyDir, false, true);
		CopyDir.Reset();
	}
}

FString FHilTwinApi::LastError() const
{
	if (!LastErrorFn)
	{
		return TEXT("twin library not loaded");
	}
	const char* M = LastErrorFn();
	return M ? FString(UTF8_TO_TCHAR(M)) : FString();
}
