/**
 * @file HilTwinTests.cpp
 * @brief Automation tests of the plugin against the real twin library (no map, no GPU).
 *
 *   HilTwin.Abi               the library loads from a private copy, every export binds, the ABI matches, refusals
 *                             come back as messages
 *   HilTwin.KinematicParity   a scenario played through FHilTwinScenario at the twin step gives the SAME trace hash
 *                             as the native runner (HIL_TWIN_EXPECT_HASH, printed by `hil_twin ... --seed N`)
 *
 * Inputs (environment, so CI and agents pass them without editing code):
 *   HIL_TWIN_DLL          the built hil_twin_api library
 *   HIL_TWIN_PARAMS       a params file
 *   HIL_TWIN_SCENARIO     a scenario file (KinematicParity)
 *   HIL_TWIN_SEED         the seed (default 1)
 *   HIL_TWIN_EXPECT_HASH  16 hex digits from the native runner (KinematicParity)
 * Run: UnrealEditor-Cmd.exe <Host>.uproject -ExecCmds="Automation RunTests HilTwin;Quit" -unattended -nullrhi
 */
#include "HAL/PlatformMisc.h"
#include "HilTwinApi.h"
#include "HilTwinScenario.h"
#include "Misc/AutomationTest.h"

#if WITH_AUTOMATION_TESTS

namespace
{
	/** An environment variable, or empty. */
	FString Env(const TCHAR* Name) { return FPlatformMisc::GetEnvironmentVariable(Name); }
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FHilTwinAbiTest, "HilTwin.Abi",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)

bool FHilTwinAbiTest::RunTest(const FString& Parameters)
{
	const FString Dll = Env(TEXT("HIL_TWIN_DLL")), Params = Env(TEXT("HIL_TWIN_PARAMS"));
	if (Dll.IsEmpty() || Params.IsEmpty())
	{
		AddError(TEXT("set HIL_TWIN_DLL and HIL_TWIN_PARAMS (an automation test that skips silently would look green)"));
		return false;
	}
	FHilTwinApi Api;
	FString Err;
	if (!TestTrue(TEXT("library loads and binds"), Api.Load(Dll, Err)))
	{
		AddError(Err);
		return false;
	}
	TestFalse(TEXT("second load on a live table is refused"), Api.Load(Dll, Err));

	// ---- a bad params path is refused with a message, not a crash
	TestNull(TEXT("missing params refused"), Api.Create("does/not/exist.txt", 1, nullptr));
	TestFalse(TEXT("refusal carries a message"), Api.LastError().IsEmpty());

	// ---- a real twin: dt, one joint, unknown names refused
	const FTCHARToUTF8 P8(*Params);
	void* H = Api.Create(P8.Get(), 1, nullptr);
	if (!TestNotNull(TEXT("twin created"), H))
	{
		AddError(Api.LastError());
		return false;
	}
	TestEqual(TEXT("dt is the firmware tick"), Api.Dt(H), 0.001, 1e-12);
	TestEqual(TEXT("one joint"), Api.JointCount(H), 1);
	double V = 0.0;
	TestEqual(TEXT("unknown name refused"), Api.GetScalar(H, "no.such.value", &V), -1);
	TestEqual(TEXT("unknown command refused"), Api.SendCommand(H, "launch", 1.0), -1);
	TestEqual(TEXT("steps"), Api.Step(H, 100), 0);
	Api.Destroy(H);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FHilTwinParityTest, "HilTwin.KinematicParity",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)

bool FHilTwinParityTest::RunTest(const FString& Parameters)
{
	const FString Dll = Env(TEXT("HIL_TWIN_DLL")), Params = Env(TEXT("HIL_TWIN_PARAMS"));
	const FString Scn = Env(TEXT("HIL_TWIN_SCENARIO")), Expect = Env(TEXT("HIL_TWIN_EXPECT_HASH")).ToLower();
	const FString SeedS = Env(TEXT("HIL_TWIN_SEED"));
	const uint64 Seed = SeedS.IsEmpty() ? 1 : FCString::Strtoui64(*SeedS, nullptr, 10);
	if (Dll.IsEmpty() || Params.IsEmpty() || Scn.IsEmpty() || Expect.IsEmpty())
	{
		AddError(TEXT("set HIL_TWIN_DLL, HIL_TWIN_PARAMS, HIL_TWIN_SCENARIO and HIL_TWIN_EXPECT_HASH"));
		return false;
	}
	FHilTwinApi Api;
	FHilTwinScenario S;
	FString Err;
	if (!Api.Load(Dll, Err) || !S.LoadFile(Scn, Err))
	{
		AddError(Err);
		return false;
	}
	const FTCHARToUTF8 P8(*Params);
	void* H = Api.Create(P8.Get(), Seed, nullptr);
	if (!H)
	{
		AddError(Api.LastError());
		return false;
	}

	// ---- exactly the native runner's loop: events due at t = k dt, then one step
	const double Dt = Api.Dt(H);
	const int64 Steps = (int64)FMath::RoundToDouble(S.DurationS / Dt);
	for (int64 K = 0; K < Steps; ++K)
	{
		if (!S.ApplyDue(Api, H, (double)K * Dt, Err) || Api.Step(H, 1) != 0)
		{
			AddError(Err.IsEmpty() ? Api.LastError() : Err);
			Api.Destroy(H);
			return false;
		}
		uint8_t Buf[512];
		while (Api.LinkRx(H, Buf, sizeof Buf) > 0)
		{
		}
	}
	const FString Got = FString::Printf(TEXT("%016llx"), (unsigned long long)Api.TraceHash(H));
	Api.Destroy(H);
	AddInfo(FString::Printf(TEXT("%s seed %llu: unreal %s, native %s"), *S.Name, Seed, *Got, *Expect));
	return TestEqual(TEXT("trace hash equals the native runner's"), Got, Expect);
}

#endif // WITH_AUTOMATION_TESTS
