# Conditional vendor integration

Install only for the detected stack. Existing local skills take precedence over duplicate installs. The selected global installation includes Superpowers; the eight local skills provide the evidence/memory integration. Vendor skills are instructions, not executable verification.

| Detection | Official source | Selection |
|---|---|---|
| .csproj / .sln / .slnx | https://github.com/dotnet/skills | dotnet-test, dotnet-aspnetcore, dotnet-data according to changed area; use native marketplace `dotnet-agent-skills` |
| Angular package.json | https://github.com/angular/skills | angular-developer; build with existing package scripts |
| Mastra dependency | https://github.com/mastra-ai/skills | inspect skills/mastra current entrypoint and linked version-aware docs |
| Microsoft Agent Framework C# | https://github.com/microsoft/agent-framework | skills/agent-framework-builder-dotnet |
| Azure OpenAI .NET | https://github.com/microsoft/skills | .github/plugins/azure-sdk-dotnet/skills/azure-ai-openai-dotnet |
| Azure AI Search .NET | same Microsoft repository | .github/plugins/azure-sdk-dotnet/skills/azure-search-documents-dotnet |
| Azure Service Bus .NET | same Microsoft repository | .github/plugins/azure-sdk-dotnet/skills/azure-servicebus-dotnet |
| Azure Functions / service infrastructure | https://github.com/MicrosoftDocs/Agent-Skills | skills/azure-functions, azure-service-bus, azure-cognitive-search as applicable |

From this source checkout use `python scripts/vendor_skills.py --stack <name>`; from an installed runtime use `python <CODEX_HOME>/autonomous-se/runtime/scripts/vendor_skills.py --stack <name>`. The command installs a reviewed stack selection into user skill discovery with its upstream commit recorded. It downloads from a specific resolved commit using the bundled skill installer; it never executes downloaded scripts. Subsequent source/API changes require deliberate review. Do not install Python-specific Agent Framework guidance for a C# project.

Run `--inspect` before installation, then supply the printed full SHA through `--ref`. Use `--method git` if a Windows ZIP extraction fails on unrelated long repository paths. Angular developer and Agent Framework .NET were installed from reviewed immutable commits; Superpowers and .NET test use native plugins. Mastra/Azure skills remain conditional to avoid adding unrelated guidance to this Python runtime project. In particular, current Mastra guidance includes autonomous vendor feedback: respect the user's explicit no-send-without-authorization rule before any such external action.
