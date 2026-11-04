/**
 * @file HilTwinApi.h
 * @brief Run-time binding of the twin's C ABI (sim/twin/include/hil_twin/hil_twin_api.h) inside Unreal.
 *
 * The shared library is loaded from a PRIVATE COPY under <Project>/Saved/HilTwin/<guid>/: a loaded DLL is locked on
 * Windows, so without the copy a rebuild of the twin fails while the editor is open, and every load of a copy is a
 * distinct module with its own statics. Every export is bound by name; a missing required export refuses the load
 * with one message naming all of them, and the ABI version must match the header the plugin was built against.
 */
#pragma once

#include "CoreMinimal.h"

#include <cstdint>

/**
 * @class FHilTwinApi
 * @brief Function table of one loaded copy of the twin library. Not copyable; Unload() or the destructor frees it.
 */
class HILTWIN_API FHilTwinApi
{
public:
	FHilTwinApi() = default;
	~FHilTwinApi() { Unload(); }
	FHilTwinApi(const FHilTwinApi&) = delete;
	FHilTwinApi& operator=(const FHilTwinApi&) = delete;

	/**
	 * @brief Copy the library to a private folder, load the copy, bind every export, check the ABI version.
	 * @param SourcePath  the built hil_twin_api library (absolute, or relative to the project directory)
	 * @param OutError    why it failed
	 * @return true when every required export is bound and the ABI matches
	 */
	bool Load(const FString& SourcePath, FString& OutError);

	/** @brief Free the module and delete the private copy (safe to call twice). */
	void Unload();

	/** @brief True after a successful Load. */
	bool IsLoaded() const { return Module != nullptr; }

	/** @brief The library's message for the last failing call on this thread. */
	FString LastError() const;

	// ---- the bound exports (signatures mirror hil_twin_api.h exactly)
	int (*AbiVersion)() = nullptr;
	const char* (*LastErrorFn)() = nullptr;
	void* (*Create)(const char* Params, uint64_t Seed, const char* Trace) = nullptr;
	void (*Destroy)(void* H) = nullptr;
	double (*Dt)(const void* H) = nullptr;
	double (*Time)(const void* H) = nullptr;
	int (*Step)(void* H, int N) = nullptr;
	int (*StepExternal)(void* H, const double* Q, const double* Qd, double* Tau, int NJoints) = nullptr;
	int (*JointCount)(const void* H) = nullptr;
	int (*GetJoint)(const void* H, int I, double* Q, double* Qd, double* Tau) = nullptr;
	int (*JointProps)(const void* H, int I, double* Out7) = nullptr;
	int (*LinkTx)(void* H, const uint8_t* Bytes, int N) = nullptr;
	int (*LinkRx)(void* H, uint8_t* Out, int Cap) = nullptr;
	int (*SendCommand)(void* H, const char* Name, double Value) = nullptr;
	int (*GetScalar)(const void* H, const char* Name, double* Out) = nullptr;
	int (*SetScalar)(void* H, const char* Name, double Value) = nullptr;
	uint64_t (*TraceHash)(const void* H) = nullptr;

private:
	void* Module = nullptr;
	FString CopyDir;
};
