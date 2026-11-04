// Game target of the HilTwin host project (headless -game runs of a twin map).
using UnrealBuildTool;

public class HilTwinHostTarget : TargetRules
{
	public HilTwinHostTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("HilTwinHost");
	}
}
