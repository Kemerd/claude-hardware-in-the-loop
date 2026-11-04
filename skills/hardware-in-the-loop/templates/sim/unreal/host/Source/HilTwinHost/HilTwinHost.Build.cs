// The host project's own module: empty on purpose; everything lives in the HilTwin plugin.
using UnrealBuildTool;

public class HilTwinHost : ModuleRules
{
	public HilTwinHost(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
	}
}
