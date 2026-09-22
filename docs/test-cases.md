# Test cases

Directory of the declarative scenarios under [`test_cases/`](../test_cases), consumed by `run_test.py`. Active test cases use the `<type>-<three-digit-ID>-<description>.csv` naming convention so cases of the same type stay grouped and sort numerically in directory listings.

## Active test cases

| Type | ID | Test case | Purpose |
|---|---:|---|---|
| E2E | 001 | [`e2e-001-verify_dotnet_info.csv`](../test_cases/e2e-001-verify_dotnet_info.csv) | Runs `dotnet --info`, archives its output as text and an image, and verifies that every installed SDK and runtime uses a stable `major.minor.patch` version. |
| E2E | 002 | [`e2e-002-patch_older_sdk_runtimes.csv`](../test_cases/e2e-002-patch_older_sdk_runtimes.csv) | Verifies that newer SDKs publish .NET 10, 9, and 8 applications with the expected patched runtimes, using preconfigured older-runtime feeds. |
| E2E | 003 | [`e2e-003-template_test.csv`](../test_cases/e2e-003-template_test.csv) | Verifies the latest .NET framework offered by the Visual Studio Insiders MAUI and Console App project wizards, then builds and runs the console app. |
| E2E | 007 | [`e2e-007-single_tfm_vs.csv`](../test_cases/e2e-007-single_tfm_vs.csv) | Creates a single-target C# Console App in Visual Studio, verifies the selected framework in project properties and the project file, then builds and runs it. |
| E2E | 008 | [`e2e-008-multi_tfm_scenario.csv`](../test_cases/e2e-008-multi_tfm_scenario.csv) | Creates a C# Console App targeting `net10.0` and `net48`, edits its code and project file, then builds and runs it against each target framework. |
| E2E | 009 | [`e2e-009-dotnet_lut.csv`](../test_cases/e2e-009-dotnet_lut.csv) | Creates a .NET Standard class library and xUnit tests, exercises passing and intentionally failing tests in Test Explorer and Live Unit Testing, and inspects the source files. |
| E2E | 010 | [`e2e-010-dotnet_test_explorer.csv`](../test_cases/e2e-010-dotnet_test_explorer.csv) | Creates a .NET Standard class library with xUnit, NUnit, and MSTest projects, references the library from each, and exercises Test Explorer and Live Unit Testing. |
| E2E | 012 | [`e2e-012-vb_on_dotnet.csv`](../test_cases/e2e-012-vb_on_dotnet.csv) | Creates a VB.NET console app referencing .NET and .NET Standard C# class libraries, then builds, runs, and validates the solution output in Visual Studio. |
| E2E | 014 | [`e2e-014-fsharp.csv`](../test_cases/e2e-014-fsharp.csv) | Creates an F# console app, two F# class libraries, and an xUnit project, then validates project references, build and run output, Test Explorer, and Live Unit Testing. |
| E2E | 016 | [`e2e-016-lower_version_build.csv`](../test_cases/e2e-016-lower_version_build.csv) | Copies archived .NET 8 and .NET 9 console projects from the team share, extracts them, then builds and runs each solution in Visual Studio. |
| E2E | 017 | [`e2e-017-retarget_version.csv`](../test_cases/e2e-017-retarget_version.csv) | Copies archived console projects from the team share, retargets their frameworks to .NET 10 in Visual Studio, then builds, runs, and validates them. |
| E2E | 020 | [`e2e-020-build_compile_run_NET6_MAUI_app_to_reunion.csv`](../test_cases/e2e-020-build_compile_run_NET6_MAUI_app_to_reunion.csv) | Creates, verifies, builds, and runs a MAUI app for every .NET framework offered by the Visual Studio Insiders wizard, including validating the Windows target and app interaction. |
| Productivity | 001 | [`prod-001-cs_console_app.csv`](../test_cases/prod-001-cs_console_app.csv) | Creates a C# Console App in Visual Studio, validates its project settings, applies code changes with Hot Reload, and verifies the updated output. |
| Productivity | 002 | [`prod-002-hot_reload.csv`](../test_cases/prod-002-hot_reload.csv) | Creates and runs a Razor Pages project, edits its page model and markup, applies Hot Reload, and verifies the updated browser content. |
| Productivity | 003 | [`prod-003-dotnet_core_cs.csv`](../test_cases/prod-003-dotnet_core_cs.csv) | Exercises C# console and class-library development in Visual Studio for every installed ASP.NET Core major version, including editing, debugging, navigation, packing, and publishing. |
| Productivity | 003 | [`prod-003-dotnet_core_vb.csv`](../test_cases/prod-003-dotnet_core_vb.csv) | Runs the Visual Basic variant of the .NET Core scenario for every installed ASP.NET Core major version. |
| Productivity | 004 | [`prod-004-edit_file_dependencies.csv`](../test_cases/prod-004-edit_file_dependencies.csv) | Edits a C# project file in Visual Studio, manages target frameworks, package and project references, and compile items, then updates a NuGet package. |
| Productivity | 005 | [`prod-005-fsharp_xunit.csv`](../test_cases/prod-005-fsharp_xunit.csv) | Creates F# library and xUnit projects, runs passing and failing tests, debugs the failure, fixes it, and verifies both tests pass. |
| Productivity | 006 | [`prod-006-multi_project_build.csv`](../test_cases/prod-006-multi_project_build.csv) | Creates a multi-project F# solution, validates IntelliSense, rebuilds and debugs it, and inspects project properties. |

[`_template.csv`](../test_cases/_template.csv) is the authoring template and is not an executable catalog entry.

## Legacy `v0` test cases

These earlier scenarios remain available under [`test_cases/v0/`](../test_cases/v0) but do not use the active naming convention.

| Test case | Purpose |
|---|---|
| [`console_app.csv`](../test_cases/v0/console_app.csv) | Creates, configures, builds, and runs a C# Console App in Visual Studio. |
| [`dotnet_sdk_version.csv`](../test_cases/v0/dotnet_sdk_version.csv) | Validates installed .NET SDK and ASP.NET Core runtime versions, then builds and publishes a console app for each installed major version. |
| [`razor_breakpoint.csv`](../test_cases/v0/razor_breakpoint.csv) | Creates a Razor Pages project and exercises Visual Studio breakpoint and stepping behavior. |
| [`testcase1_console_nu1605.csv`](../test_cases/v0/testcase1_console_nu1605.csv) | Creates a C# Console App and edits its project file to reproduce and suppress NuGet warning NU1605. |
| [`vs_nuget.csv`](../test_cases/v0/vs_nuget.csv) | Creates a mixed C#/VB solution, installs and updates NuGet packages, and verifies the solution builds. |
