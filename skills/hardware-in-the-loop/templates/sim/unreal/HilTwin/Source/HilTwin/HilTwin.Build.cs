// =====================================================================================================================
//  HilTwin.Build.cs - UnrealBuildTool rules of the HilTwin runtime module.
//
//  The twin itself is NOT compiled here: it is the same hil_twin_api shared library the native runner, CI and the
//  Isaac harness load, built by CMake from sim/twin. This module only loads it at run time (FPlatformProcess::
//  GetDllHandle) and binds its C exports by name, so the plugin never drifts from the tested twin.
//  The C ABI header is reached through HIL_TWIN_INCLUDE_DIR (default: sim/twin/include next to sim/unreal).
// =====================================================================================================================

using System.IO;
using UnrealBuildTool;

public class HilTwin : ModuleRules
{
	public HilTwin(ReadOnlyTargetRules Target) : base(Target)
	{
		// ---- explicit PCHs, C++20, no exceptions: the twin's ABI is plain C and throws nothing
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		CppStandard = CppStandardVersion.Cpp20;
		bEnableExceptions = false;

		// ---- engine modules this module uses (each one is referenced in the sources)
		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"PhysicsCore",
			"Chaos",
			"Projects"
		});

		// ---- the twin's C ABI header: HIL_TWIN_INCLUDE_DIR, else the first ancestor of this module that holds
		//      twin/include or sim/twin/include (works from sim/unreal/HilTwin; a host that reaches the plugin through
		//      a Plugins/ junction sets HIL_TWIN_INCLUDE_DIR or keeps the host next to sim/)
		string IncludeDir = System.Environment.GetEnvironmentVariable("HIL_TWIN_INCLUDE_DIR");
		if (string.IsNullOrEmpty(IncludeDir))
		{
			IncludeDir = "";
			for (DirectoryInfo Dir = new DirectoryInfo(ModuleDirectory); Dir != null && IncludeDir.Length == 0; Dir = Dir.Parent)
			{
				foreach (string Rel in new string[] { Path.Combine("twin", "include"), Path.Combine("sim", "twin", "include") })
				{
					string Candidate = Path.Combine(Dir.FullName, Rel);
					if (File.Exists(Path.Combine(Candidate, "hil_twin", "hil_twin_api.h")))
					{
						IncludeDir = Candidate;
						break;
					}
				}
			}
		}
		if (!File.Exists(Path.Combine(IncludeDir, "hil_twin", "hil_twin_api.h")))
		{
			throw new BuildException("HilTwin: hil_twin/hil_twin_api.h not found under " + IncludeDir +
			                         " (set HIL_TWIN_INCLUDE_DIR to sim/twin/include)");
		}
		PrivateIncludePaths.Add(IncludeDir);

		// ---- the header's dllimport branch is not wanted: symbols are bound by name at run time
		PrivateDefinitions.Add("HIL_TWIN_STATIC=1");
	}
}
