/**
 * @file HilTwinScenario.h
 * @brief The timeline part of a sim/twin scenario file (.scn), played into a twin handle with the SAME rule as the
 *        native runner (sim/twin/src/twin_main.c): before step k, at t = k dt, every event with t_event <= t + 1e-12
 *        that has not fired yet fires, in file order. Same rule, same seed, same library: the same trace hash, which
 *        is how the Unreal host is checked against the native one.
 *
 * Only "name", "duration_s" and "at" lines are used here; "accept" lines are the native runner's verdict and are
 * skipped (Unreal plays the scenario, the runner or CI judges it).
 */
#pragma once

#include "CoreMinimal.h"

class FHilTwinApi;

/** @brief One timed event. */
struct FHilTwinEvent
{
	double T = 0.0;
	bool bIsSet = false;      ///< true: SetScalar(Name, Value); false: SendCommand(Name, Value)
	FString Name;
	double Value = 0.0;
	bool bDone = false;
};

/**
 * @class FHilTwinScenario
 * @brief Parsed timeline plus its playback cursor.
 */
class HILTWIN_API FHilTwinScenario
{
public:
	/** @brief Parse a file. @return false with OutError naming the line on any problem. */
	bool LoadFile(const FString& Path, FString& OutError);

	/** @brief Fire every due event before the step that starts at TimeS. @return false when the twin refused one. */
	bool ApplyDue(FHilTwinApi& Api, void* Handle, double TimeS, FString& OutError);

	/** @brief True once a scenario was loaded. */
	bool IsLoaded() const { return bLoaded; }

	FString Name;
	double DurationS = 0.0;
	TArray<FHilTwinEvent> Events;

private:
	bool bLoaded = false;
};
