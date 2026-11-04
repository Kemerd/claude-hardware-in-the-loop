/**
 * @file HilTwinModule.cpp
 * @brief Module entry, log category and the console commands agents use in headless runs:
 *
 *     HilTwin.Arm 1|0              arm / disarm every twin actor in every world
 *     HilTwin.SetPos <rad>         joint target
 *     HilTwin.Stop                 hold position
 *     HilTwin.Set <name> <value>   environment value or fault injection (ambient_c, ext_torque_nm, fault.encoder ...)
 *
 * Example: UnrealEditor-Cmd.exe Host.uproject /Game/Map -game -nullrhi -unattended -ExecCmds="HilTwin.Arm 1"
 */
#include "HilTwinModule.h"

#include "Engine/World.h"
#include "EngineUtils.h"
#include "HAL/IConsoleManager.h"
#include "HilTwinJointActor.h"
#include "Modules/ModuleManager.h"

DEFINE_LOG_CATEGORY(LogHilTwin);

namespace
{
	/** Queue a command on every twin actor of the world. */
	void Broadcast(UWorld* World, bool bIsSet, const FString& Name, double Value)
	{
		if (!World)
		{
			return;
		}
		int32 N = 0;
		for (TActorIterator<AHilTwinJointActor> It(World); It; ++It)
		{
			It->QueueCommand(bIsSet, Name, Value);
			++N;
		}
		UE_LOG(LogHilTwin, Display, TEXT("%s %s %g -> %d twin actor(s)"), bIsSet ? TEXT("set") : TEXT("command"), *Name, Value, N);
	}

	FAutoConsoleCommandWithWorldAndArgs CmdArm(TEXT("HilTwin.Arm"), TEXT("HilTwin.Arm 1|0"),
		FConsoleCommandWithWorldAndArgsDelegate::CreateLambda([](const TArray<FString>& A, UWorld* W)
		{
			Broadcast(W, false, TEXT("arm"), A.Num() > 0 ? FCString::Atod(*A[0]) : 1.0);
		}));
	FAutoConsoleCommandWithWorldAndArgs CmdPos(TEXT("HilTwin.SetPos"), TEXT("HilTwin.SetPos <rad>"),
		FConsoleCommandWithWorldAndArgsDelegate::CreateLambda([](const TArray<FString>& A, UWorld* W)
		{
			if (A.Num() > 0)
			{
				Broadcast(W, false, TEXT("set_pos"), FCString::Atod(*A[0]));
			}
		}));
	FAutoConsoleCommandWithWorldAndArgs CmdStop(TEXT("HilTwin.Stop"), TEXT("HilTwin.Stop"),
		FConsoleCommandWithWorldAndArgsDelegate::CreateLambda([](const TArray<FString>& A, UWorld* W)
		{
			Broadcast(W, false, TEXT("stop"), 0.0);
		}));
	FAutoConsoleCommandWithWorldAndArgs CmdSet(TEXT("HilTwin.Set"), TEXT("HilTwin.Set <name> <value>"),
		FConsoleCommandWithWorldAndArgsDelegate::CreateLambda([](const TArray<FString>& A, UWorld* W)
		{
			if (A.Num() > 1)
			{
				Broadcast(W, true, A[0], FCString::Atod(*A[1]));
			}
		}));
}

IMPLEMENT_MODULE(FDefaultModuleImpl, HilTwin)
