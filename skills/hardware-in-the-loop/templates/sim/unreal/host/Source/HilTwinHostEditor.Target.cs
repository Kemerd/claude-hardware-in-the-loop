// Editor target of the HilTwin host project (build the plugin, run its automation tests, edit twin maps).
using UnrealBuildTool;

public class HilTwinHostEditorTarget : TargetRules
{
	public HilTwinHostEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("HilTwinHost");
	}
}
