# Single TFM (VS)

## Prerequisites

- Ensure that the **.NET desktop development** workload is installed for Visual Studio.
- Ensure that at least one current .NET SDK is installed so Visual Studio offers a default (single) target framework when creating a Console App.

## Test steps

1. Create a .NET console app.

   1. Launch Visual Studio and select **Create a new project**.
   2. Filter templates to **C#**, search for `console app`, and select the **Console App** template.
   3. Name the project `SingleTfmVsConsole` in a solution named `SingleTfmVs`, using a fixed, portable location under the current user's home directory.
   4. On the **Additional information** page, note the default **Framework** value offered by the wizard (a single target framework, e.g. `.NET 9.0`). Do not change it.
   5. Click **Create**.

2. Check what fx the project is targeting.

   1. In **Solution Explorer**, right-click `SingleTfmVsConsole` and select **Properties**.
   2. Read the **Target framework** field and confirm it matches the framework noted in step 1.4 (e.g. `.NET 9.0`).
   3. Open `SingleTfmVsConsole.csproj` (right-click the project > **Edit Project File**, or inspect the file directly) and confirm the single `<TargetFramework>` element (e.g. `net9.0`) matches the same version, and that there is only one target framework (not `<TargetFrameworks>` with multiple values).

3. Build and run.

   1. Build the solution (**Build > Build Solution**, or Ctrl+Shift+B).
   2. Verify the build succeeds with 0 errors in the Output window.
   3. Run the app without debugging (Ctrl+F5).
   4. Verify the console window opens and shows the default template output (e.g. `Hello, World!`).

4. Report the result.

   1. In the test results report for this test case, record the exact target fx the project is targeting (the value captured/verified in step 2).
   2. If the target fx does not seem right (e.g. an unexpected/mismatched or outdated version), report it as a bug instead of just noting it.
