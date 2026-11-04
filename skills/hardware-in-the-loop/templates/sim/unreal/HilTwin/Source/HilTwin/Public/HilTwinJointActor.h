/**
 * @file HilTwinJointActor.h
 * @brief An actor that hosts one twin (one firmware image + its plant) and shows its joint in the level.
 *
 * Two coupling modes:
 *
 *   Kinematic   (default, deterministic) the twin integrates everything at its own fixed step; Tick runs as many
 *               whole steps as the frame time allows and poses the link. Same scenario, same seed, same library as
 *               the native runner: the same trace hash. Use it for visual review, demos, camera and sensor rendering.
 *
 *   ChaosTorque Chaos owns the link (a simulated body on a hinge constraint, with the twin's link mass, CoM and
 *               axis inertia plus the reflected rotor inertia); every async physics step the actor reads the hinge
 *               angle and rate, hands them to hil_twin_step_external, and applies the returned actuator torque.
 *               Use it when the link touches the world (contacts, payloads, other bodies). Needs Project Settings >
 *               Physics > Tick Physics Async on, with the async fixed step equal to the twin step (0.001 s).
 *
 * Frames: the link mesh points along the pivot's +X; joint angle q = 0 is +X horizontal, q > 0 lifts +X towards
 * +Z (FRotator pitch). Units: the twin is SI, Unreal is cm: forces and torques are scaled on the way in.
 * Threading: commands from Blueprint go through a queue drained by the stepping thread (game thread in Kinematic,
 * physics thread in ChaosTorque); every twin call is made under one lock, so Blueprint reads are safe in both modes.
 */
#pragma once

#include "Containers/Queue.h"
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "HAL/CriticalSection.h"
#include "HilTwinApi.h"
#include "HilTwinScenario.h"

#include "HilTwinJointActor.generated.h"

class UStaticMeshComponent;
class UPhysicsConstraintComponent;

/** @brief Who integrates the link. */
UENUM(BlueprintType)
enum class EHilTwinCoupling : uint8
{
	Kinematic UMETA(DisplayName = "Kinematic (twin integrates, deterministic)"),
	ChaosTorque UMETA(DisplayName = "Chaos torque (Chaos integrates, twin drives)")
};

UCLASS(Blueprintable, ClassGroup = (Simulation))
class HILTWIN_API AHilTwinJointActor : public AActor
{
	GENERATED_BODY()

public:
	AHilTwinJointActor();

	// ---- configuration (command line overrides: -HilTwinLibrary= -HilTwinParams= -HilTwinScenario= -HilTwinSeed=)

	/** The built hil_twin_api library (absolute, or relative to the project directory). */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "HilTwin")
	FFilePath TwinLibrary;

	/** The twin params file (sim/twin/params/...). */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "HilTwin")
	FFilePath ParamsFile;

	/** Optional scenario (.scn) whose timeline is played at the twin's step. */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "HilTwin")
	FFilePath ScenarioFile;

	/** Run seed (noise streams and per-unit tolerances). */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "HilTwin")
	int64 Seed = 1;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "HilTwin")
	EHilTwinCoupling Coupling = EHilTwinCoupling::Kinematic;

	/** Write the twin's CSV trace to Saved/HilTwin/<scenario>_s<seed>.csv. */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "HilTwin")
	bool bWriteTrace = true;

	/** Kinematic mode: at most this many twin steps per frame (a hitch never turns into a stall). */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "HilTwin", meta = (ClampMin = "1", ClampMax = "10000"))
	int32 MaxStepsPerFrame = 250;

	/** Quit the application when the scenario has run its duration (headless runs: also -HilTwinQuitOnFinish). */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "HilTwin")
	bool bQuitOnFinish = false;

	// ---- components

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "HilTwin")
	TObjectPtr<USceneComponent> Root;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "HilTwin")
	TObjectPtr<UStaticMeshComponent> BaseMesh;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "HilTwin")
	TObjectPtr<USceneComponent> Pivot;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "HilTwin")
	TObjectPtr<UStaticMeshComponent> LinkMesh;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "HilTwin")
	TObjectPtr<UPhysicsConstraintComponent> Hinge;

	// ---- Blueprint API (all thread-safe; commands are queued for the stepping thread)

	/** Arm (home, then hold) or disarm (motor off, clears faults). */
	UFUNCTION(BlueprintCallable, Category = "HilTwin")
	void Arm(bool bArm);

	/** Commanded joint target, rad. */
	UFUNCTION(BlueprintCallable, Category = "HilTwin")
	void SetTargetRad(float TargetRad);

	/** Hold the present position. */
	UFUNCTION(BlueprintCallable, Category = "HilTwin")
	void Stop();

	/** Environment value or fault injection (ambient_c, soak_c, ext_torque_nm, fault.encoder, fault.ntc_open). */
	UFUNCTION(BlueprintCallable, Category = "HilTwin")
	void SetTwinValue(const FString& Name, float Value);

	/** A named twin value (q_rad, t_winding_c, vbat_v, fw.state ...). @return false when unknown or not running */
	UFUNCTION(BlueprintCallable, Category = "HilTwin")
	bool GetTwinValue(const FString& Name, float& OutValue) const;

	/** True while a twin is loaded and stepping. */
	UFUNCTION(BlueprintPure, Category = "HilTwin")
	bool IsTwinRunning() const { return bReady; }

	/** Twin trace hash so far (determinism fingerprint), as 16 hex digits. */
	UFUNCTION(BlueprintPure, Category = "HilTwin")
	FString GetTraceHash() const;

	/** Queue a link command by name (arm / set_pos / stop) or an environment value (bIsSet). Console commands use it. */
	void QueueCommand(bool bIsSet, const FString& Name, double Value);

protected:
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type Reason) override;
	virtual void Tick(float DeltaSeconds) override;
	virtual void AsyncPhysicsTickActor(float DeltaTime, float SimTime) override;

private:
	/** A queued command (game thread -> stepping thread). */
	struct FPending
	{
		bool bIsSet = false;
		FString Name;
		double Value = 0.0;
	};

	/** Load the library, create the twin, load the scenario, set up the coupling. */
	bool StartTwin(FString& OutError);
	/** Drain the queue and the scenario before one step. Caller holds TwinLock. */
	bool BeforeStep(FString& OutError);
	/** After a step: finish detection, logging. Caller holds TwinLock. */
	void AfterStep();
	/** ChaosTorque: mass, inertia, CoM and hinge from the twin's joint properties. */
	bool SetupChaos(FString& OutError);
	/** Stop stepping and report why. */
	void Fail(const FString& Why);

	FHilTwinApi Api;
	void* Handle = nullptr;
	FHilTwinScenario Scenario;
	mutable FCriticalSection TwinLock;
	TQueue<FPending, EQueueMode::Mpsc> Pending;
	double TwinDt = 0.001;
	double Accum = 0.0;
	bool bReady = false;
	bool bFinished = false;
	FString RunName;
	/** ChaosTorque: hinge frame in the world (the base does not move). */
	FVector AxisWorld = FVector::ZeroVector;
	FVector FwdWorld = FVector::ForwardVector;
	FVector UpWorld = FVector::UpVector;
	bool bWarnedDt = false;
};
