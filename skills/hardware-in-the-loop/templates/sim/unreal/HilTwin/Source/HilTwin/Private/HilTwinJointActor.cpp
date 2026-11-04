/**
 * @file HilTwinJointActor.cpp
 * @brief Twin host actor: start-up, the two coupling modes, the Blueprint / console command path.
 */
#include "HilTwinJointActor.h"

#include "Components/StaticMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/StaticMesh.h"
#include "HAL/PlatformMisc.h"
#include "HilTwinModule.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "PhysicsEngine/BodyInstance.h"
#include "PhysicsEngine/PhysicsConstraintComponent.h"
#include "PhysicsEngine/PhysicsSettings.h"
#include "PhysicsProxy/SingleParticlePhysicsProxy.h"
#include "UObject/ConstructorHelpers.h"

namespace
{
	/** m -> cm for lengths, N m -> kg cm^2 / s^2 for torques, kg m^2 -> kg cm^2 for inertia. */
	constexpr double M_TO_CM = 100.0;
	constexpr double NM_TO_UE = 10000.0;
	constexpr double KGM2_TO_UE = 10000.0;
}

/* ---------------------------------------------------------------------------------------------------------------------
 *  Construction: base block, pivot, link bar (engine cube, 30 cm along +X), hinge at the pivot
 * --------------------------------------------------------------------------------------------------------------------- */

AHilTwinJointActor::AHilTwinJointActor()
{
	PrimaryActorTick.bCanEverTick = true;
	bAsyncPhysicsTickEnabled = true;

	Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	SetRootComponent(Root);

	static ConstructorHelpers::FObjectFinder<UStaticMesh> Cube(TEXT("/Engine/BasicShapes/Cube.Cube"));

	// ---- base: a block under the pivot, never simulated (the rig is bolted to the bench)
	BaseMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Base"));
	BaseMesh->SetupAttachment(Root);
	BaseMesh->SetRelativeScale3D(FVector(0.1, 0.1, 0.1));
	BaseMesh->SetMobility(EComponentMobility::Static);

	Pivot = CreateDefaultSubobject<USceneComponent>(TEXT("Pivot"));
	Pivot->SetupAttachment(Root);

	// ---- link: replace the mesh with the CAD export; its +X must point away from the joint axis
	LinkMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Link"));
	LinkMesh->SetupAttachment(Pivot);
	LinkMesh->SetRelativeLocation(FVector(15.0, 0.0, 0.0));
	LinkMesh->SetRelativeScale3D(FVector(0.3, 0.04, 0.04));

	if (Cube.Succeeded())
	{
		BaseMesh->SetStaticMesh(Cube.Object);
		LinkMesh->SetStaticMesh(Cube.Object);
	}

	Hinge = CreateDefaultSubobject<UPhysicsConstraintComponent>(TEXT("Hinge"));
	Hinge->SetupAttachment(Pivot);
}

/* ---------------------------------------------------------------------------------------------------------------------
 *  Start / stop
 * --------------------------------------------------------------------------------------------------------------------- */

void AHilTwinJointActor::BeginPlay()
{
	Super::BeginPlay();
	FString Err;
	if (!StartTwin(Err))
	{
		Fail(Err);
	}
}

void AHilTwinJointActor::EndPlay(const EEndPlayReason::Type Reason)
{
	{
		FScopeLock Lock(&TwinLock);
		bReady = false;
		if (Handle && Api.Destroy)
		{
			Api.Destroy(Handle);
		}
		Handle = nullptr;
	}
	Api.Unload();
	Super::EndPlay(Reason);
}

void AHilTwinJointActor::Fail(const FString& Why)
{
	bReady = false;
	UE_LOG(LogHilTwin, Error, TEXT("%s: %s"), *GetName(), *Why);
	if (GEngine)
	{
		GEngine->AddOnScreenDebugMessage(-1, 30.f, FColor::Red, FString::Printf(TEXT("HilTwin %s: %s"), *GetName(), *Why));
	}
}

bool AHilTwinJointActor::StartTwin(FString& OutError)
{
	// ---- command line overrides, so an agent can run any scenario on any map headless
	FString Lib = TwinLibrary.FilePath, Params = ParamsFile.FilePath, Scn = ScenarioFile.FilePath;
	FParse::Value(FCommandLine::Get(), TEXT("HilTwinLibrary="), Lib);
	FParse::Value(FCommandLine::Get(), TEXT("HilTwinParams="), Params);
	FParse::Value(FCommandLine::Get(), TEXT("HilTwinScenario="), Scn);
	FParse::Value(FCommandLine::Get(), TEXT("HilTwinSeed="), Seed);
	if (FParse::Param(FCommandLine::Get(), TEXT("HilTwinQuitOnFinish")))
	{
		bQuitOnFinish = true;
	}
	auto Full = [](const FString& P) { return FPaths::IsRelative(P) ? FPaths::ConvertRelativePathToFull(FPaths::ProjectDir(), P) : P; };
	if (Params.IsEmpty())
	{
		OutError = TEXT("ParamsFile is empty (set it in Details or pass -HilTwinParams=)");
		return false;
	}
	if (!Api.Load(Lib, OutError))
	{
		return false;
	}

	// ---- the scenario first: its name names the trace
	RunName = TEXT("free");
	if (!Scn.IsEmpty())
	{
		if (!Scenario.LoadFile(Full(Scn), OutError))
		{
			return false;
		}
		RunName = Scenario.Name;
	}
	const FString Trace = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("HilTwin"), FString::Printf(TEXT("%s_s%lld.csv"), *RunName, Seed));
	const FTCHARToUTF8 P8(*Full(Params));
	const FTCHARToUTF8 T8(*Trace);
	Handle = Api.Create(P8.Get(), (uint64_t)Seed, bWriteTrace ? T8.Get() : nullptr);
	if (!Handle)
	{
		OutError = Api.LastError();
		return false;
	}
	TwinDt = Api.Dt(Handle);

	// ---- pose the link at the twin's initial angle (both modes start from the twin's truth)
	double Q = 0.0, Qd = 0.0, Tau = 0.0;
	Api.GetJoint(Handle, 0, &Q, &Qd, &Tau);
	Pivot->SetRelativeRotation(FRotator(FMath::RadiansToDegrees(Q), 0.0, 0.0));

	if (Coupling == EHilTwinCoupling::ChaosTorque && !SetupChaos(OutError))
	{
		return false;
	}
	bReady = true;
	UE_LOG(LogHilTwin, Log, TEXT("%s: twin running, %s, seed %lld, dt %.6f s, scenario '%s'"), *GetName(),
	       Coupling == EHilTwinCoupling::Kinematic ? TEXT("kinematic") : TEXT("chaos torque"), Seed, TwinDt, *RunName);
	return true;
}

bool AHilTwinJointActor::SetupChaos(FString& OutError)
{
	// ---- the async physics step must BE the twin step: the firmware runs at a fixed 1 kHz
	const UPhysicsSettings* PS = UPhysicsSettings::Get();
	if (!PS || !PS->bTickPhysicsAsync || FMath::Abs(PS->AsyncFixedTimeStepSize - TwinDt) > 1e-6)
	{
		OutError = FString::Printf(TEXT("ChaosTorque needs Project Settings > Physics > Tick Physics Async on and Async Fixed Time Step Size = %.4f s"), TwinDt);
		return false;
	}
	double Props[7] = {0};
	if (Api.JointProps(Handle, 0, Props) != 0)
	{
		OutError = Api.LastError();
		return false;
	}
	const double MLink = Props[0], LCom = Props[1], JLink = Props[2], JRefl = Props[3];

	// ---- hinge frame in the world, captured once (the base is static)
	//      (q = 0 is the ROOT's +X; the pivot is already turned to the initial angle, so it is not the reference)
	FwdWorld = Root->GetComponentTransform().GetUnitAxis(EAxis::X);
	UpWorld = Root->GetComponentTransform().GetUnitAxis(EAxis::Z);
	AxisWorld = FVector::CrossProduct(FwdWorld, UpWorld).GetSafeNormal();

	// ---- the link body: simulated, the twin's mass; CoM at l_com along the link; inertia about the hinge axis =
	//      j_link (about the axis) - m l^2 (parallel axis) + the reflected rotor inertia
	LinkMesh->SetSimulatePhysics(true);
	LinkMesh->SetEnableGravity(true);
	LinkMesh->SetMassOverrideInKg(NAME_None, (float)MLink, true);
	FBodyInstance* BI = LinkMesh->GetBodyInstance();
	if (!BI)
	{
		OutError = TEXT("link mesh has no body instance (no collision?)");
		return false;
	}
	BI->UpdateMassProperties();
	const FVector WantCom = Pivot->GetComponentLocation() + Pivot->GetComponentTransform().GetUnitAxis(EAxis::X) * (LCom * M_TO_CM);
	BI->COMNudge = LinkMesh->GetComponentTransform().InverseTransformVectorNoScale(WantCom - BI->GetCOMPosition());
	const double IWant = FMath::Max(1e-9, JLink - MLink * LCom * LCom + JRefl) * KGM2_TO_UE;
	const FVector IHave = BI->GetBodyInertiaTensor();
	if (!(IHave.Y > 0.0))
	{
		OutError = TEXT("link body reports no inertia about its Y axis");
		return false;
	}
	BI->InertiaTensorScale = FVector(IWant / IHave.Y);
	BI->UpdateMassProperties();

	// ---- hinge: linear locked, rotation free only about the pivot's Y (pitch); collision base <-> link off
	Hinge->SetConstrainedComponents(BaseMesh, NAME_None, LinkMesh, NAME_None);
	Hinge->SetLinearXLimit(LCM_Locked, 0.f);
	Hinge->SetLinearYLimit(LCM_Locked, 0.f);
	Hinge->SetLinearZLimit(LCM_Locked, 0.f);
	Hinge->SetAngularSwing1Limit(ACM_Locked, 0.f);
	Hinge->SetAngularTwistLimit(ACM_Locked, 0.f);
	Hinge->SetAngularSwing2Limit(ACM_Free, 0.f);
	Hinge->SetDisableCollision(true);
	return true;
}

/* ---------------------------------------------------------------------------------------------------------------------
 *  Stepping helpers (caller holds TwinLock)
 * --------------------------------------------------------------------------------------------------------------------- */

bool AHilTwinJointActor::BeforeStep(FString& OutError)
{
	// ---- queued Blueprint / console commands first, then the scenario's due events (the native runner's rule)
	FPending P;
	while (Pending.Dequeue(P))
	{
		const FTCHARToUTF8 N(*P.Name);
		const int Rc = P.bIsSet ? Api.SetScalar(Handle, N.Get(), P.Value) : Api.SendCommand(Handle, N.Get(), P.Value);
		if (Rc != 0)
		{
			UE_LOG(LogHilTwin, Warning, TEXT("%s: command %s refused: %s"), *GetName(), *P.Name, *Api.LastError());
		}
	}
	return Scenario.ApplyDue(Api, Handle, Api.Time(Handle), OutError);
}

void AHilTwinJointActor::AfterStep()
{
	// ---- the telemetry bytes are drained so the link queue never fills (a station would read them)
	uint8_t Buf[512];
	while (Api.LinkRx(Handle, Buf, sizeof Buf) > 0)
	{
	}
	if (Scenario.IsLoaded() && !bFinished && Api.Time(Handle) + 1e-9 >= Scenario.DurationS)
	{
		bFinished = true;
		UE_LOG(LogHilTwin, Display, TEXT("[twin-ue] %s seed %lld done t=%.3f trace fnv1a64=%016llx"), *RunName, Seed,
		       Api.Time(Handle), (unsigned long long)Api.TraceHash(Handle));
	}
}

/* ---------------------------------------------------------------------------------------------------------------------
 *  Kinematic: whole twin steps per frame, then pose the link
 * --------------------------------------------------------------------------------------------------------------------- */

void AHilTwinJointActor::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (!bReady)
	{
		return;
	}
	if (Coupling == EHilTwinCoupling::Kinematic)
	{
		double Q = 0.0;
		{
			FScopeLock Lock(&TwinLock);
			Accum += FMath::Clamp((double)DeltaSeconds, 0.0, 0.25);
			int32 N = 0;
			while (Accum + 1e-12 >= TwinDt && N < MaxStepsPerFrame && !bFinished)
			{
				FString Err;
				if (!BeforeStep(Err) || Api.Step(Handle, 1) != 0)
				{
					Fail(Err.IsEmpty() ? Api.LastError() : Err);
					return;
				}
				AfterStep();
				Accum -= TwinDt;
				++N;
			}
			if (N == MaxStepsPerFrame)
			{
				Accum = 0.0; // a long hitch: drop the backlog instead of chasing it forever
			}
			double Qd = 0.0, Tau = 0.0;
			Api.GetJoint(Handle, 0, &Q, &Qd, &Tau);
		}
		Pivot->SetRelativeRotation(FRotator(FMath::RadiansToDegrees(Q), 0.0, 0.0));
	}
	if (bFinished && bQuitOnFinish)
	{
		bQuitOnFinish = false;
		FPlatformMisc::RequestExit(false, TEXT("HilTwin scenario finished"));
	}
}

/* ---------------------------------------------------------------------------------------------------------------------
 *  ChaosTorque: every async physics step, hinge state in, actuator torque out (physics thread)
 * --------------------------------------------------------------------------------------------------------------------- */

void AHilTwinJointActor::AsyncPhysicsTickActor(float DeltaTime, float SimTime)
{
	Super::AsyncPhysicsTickActor(DeltaTime, SimTime);
	if (!bReady || Coupling != EHilTwinCoupling::ChaosTorque || bFinished)
	{
		return;
	}
	if (!bWarnedDt && FMath::Abs(DeltaTime - TwinDt) > 1e-6)
	{
		bWarnedDt = true;
		UE_LOG(LogHilTwin, Warning, TEXT("%s: async step %.6f s differs from the twin step %.6f s"), *GetName(), DeltaTime, TwinDt);
	}
	FBodyInstance* BI = LinkMesh ? LinkMesh->GetBodyInstance() : nullptr;
	FPhysicsActorHandle H = BI ? BI->GetPhysicsActor() : nullptr;
	Chaos::FRigidBodyHandle_Internal* RB = H ? H->GetPhysicsThreadAPI() : nullptr;
	if (!RB)
	{
		return;
	}

	// ---- hinge angle from the link's +X in the base frame, rate from the body's angular velocity about the axis
	const FVector F = RB->GetR().RotateVector(FVector::ForwardVector);
	double Q = FMath::Atan2(FVector::DotProduct(F, UpWorld), FVector::DotProduct(F, FwdWorld));
	double Qd = FVector::DotProduct(RB->GetW(), AxisWorld);
	double Tau = 0.0;
	{
		FScopeLock Lock(&TwinLock);
		FString Err;
		if (!BeforeStep(Err) || Api.StepExternal(Handle, &Q, &Qd, &Tau, 1) != 0)
		{
			Fail(Err.IsEmpty() ? Api.LastError() : Err);
			return;
		}
		AfterStep();
	}
	RB->AddTorque(AxisWorld * (Tau * NM_TO_UE));
}

/* ---------------------------------------------------------------------------------------------------------------------
 *  Blueprint / console API
 * --------------------------------------------------------------------------------------------------------------------- */

void AHilTwinJointActor::QueueCommand(bool bIsSet, const FString& Name, double Value)
{
	FPending P;
	P.bIsSet = bIsSet;
	P.Name = Name;
	P.Value = Value;
	Pending.Enqueue(MoveTemp(P));
}

void AHilTwinJointActor::Arm(bool bArm) { QueueCommand(false, TEXT("arm"), bArm ? 1.0 : 0.0); }

void AHilTwinJointActor::SetTargetRad(float TargetRad) { QueueCommand(false, TEXT("set_pos"), TargetRad); }

void AHilTwinJointActor::Stop() { QueueCommand(false, TEXT("stop"), 0.0); }

void AHilTwinJointActor::SetTwinValue(const FString& Name, float Value) { QueueCommand(true, Name, Value); }

bool AHilTwinJointActor::GetTwinValue(const FString& Name, float& OutValue) const
{
	FScopeLock Lock(&TwinLock);
	if (!Handle || !Api.IsLoaded())
	{
		return false;
	}
	double V = 0.0;
	const FTCHARToUTF8 N(*Name);
	if (Api.GetScalar(Handle, N.Get(), &V) != 0)
	{
		return false;
	}
	OutValue = (float)V;
	return true;
}

FString AHilTwinJointActor::GetTraceHash() const
{
	FScopeLock Lock(&TwinLock);
	return (Handle && Api.IsLoaded()) ? FString::Printf(TEXT("%016llx"), (unsigned long long)Api.TraceHash(Handle)) : FString();
}
