/**
 * @file HilTwinScenario.cpp
 * @brief .scn timeline parser and player (same rule as sim/twin/src/twin_main.c).
 */
#include "HilTwinScenario.h"

#include "HilTwinApi.h"
#include "Misc/FileHelper.h"

bool FHilTwinScenario::LoadFile(const FString& Path, FString& OutError)
{
	Events.Reset();
	Name.Reset();
	DurationS = 0.0;
	bLoaded = false;

	TArray<FString> Lines;
	if (!FFileHelper::LoadFileToStringArray(Lines, *Path))
	{
		OutError = FString::Printf(TEXT("cannot read scenario '%s'"), *Path);
		return false;
	}
	for (int32 Ln = 0; Ln < Lines.Num(); ++Ln)
	{
		// ---- strip the comment, split on whitespace
		FString L = Lines[Ln];
		int32 Hash = INDEX_NONE;
		if (L.FindChar(TEXT('#'), Hash))
		{
			L.LeftInline(Hash);
		}
		TArray<FString> F;
		L.ParseIntoArrayWS(F);
		if (F.Num() == 0)
		{
			continue;
		}
		if (F[0] == TEXT("name") && F.Num() >= 3 && F[1] == TEXT("="))
		{
			Name = F[2];
		}
		else if (F[0] == TEXT("duration_s") && F.Num() >= 3 && F[1] == TEXT("="))
		{
			DurationS = FCString::Atod(*F[2]);
		}
		else if (F[0] == TEXT("at") && F.Num() >= 3)
		{
			FHilTwinEvent E;
			E.T = FCString::Atod(*F[1]);
			if (F[2] == TEXT("set"))
			{
				if (F.Num() < 5)
				{
					OutError = FString::Printf(TEXT("%s:%d: 'set' needs a name and a value"), *Path, Ln + 1);
					return false;
				}
				E.bIsSet = true;
				E.Name = F[3];
				E.Value = FCString::Atod(*F[4]);
			}
			else
			{
				E.Name = F[2];
				E.Value = F.Num() >= 4 ? FCString::Atod(*F[3]) : 0.0;
			}
			Events.Add(E);
		}
		else if (F[0] == TEXT("accept"))
		{
			// ---- verdict lines belong to the native runner
		}
		else
		{
			OutError = FString::Printf(TEXT("%s:%d: cannot read '%s'"), *Path, Ln + 1, *F[0]);
			return false;
		}
	}
	if (Name.IsEmpty() || !(DurationS > 0.0))
	{
		OutError = FString::Printf(TEXT("%s: needs name and duration_s > 0"), *Path);
		return false;
	}
	bLoaded = true;
	return true;
}

bool FHilTwinScenario::ApplyDue(FHilTwinApi& Api, void* Handle, double TimeS, FString& OutError)
{
	if (!bLoaded || !Handle || !Api.IsLoaded())
	{
		return true;
	}
	for (FHilTwinEvent& E : Events)
	{
		if (E.bDone || TimeS + 1e-12 < E.T)
		{
			continue;
		}
		E.bDone = true;
		const FTCHARToUTF8 N(*E.Name);
		const int Rc = E.bIsSet ? Api.SetScalar(Handle, N.Get(), E.Value) : Api.SendCommand(Handle, N.Get(), E.Value);
		if (Rc != 0)
		{
			OutError = FString::Printf(TEXT("event at %.3f s (%s): %s"), E.T, *E.Name, *Api.LastError());
			return false;
		}
	}
	return true;
}
