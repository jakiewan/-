import path from 'node:path'
import { finalizePresentation } from '/Users/wanjiaheng/.codex/plugins/cache/openai-primary-runtime/presentations/26.1020.11127/skills/presentations/container_tools/artifact_tool_utils.mjs'
const root=process.cwd()
const skill='/Users/wanjiaheng/.codex/plugins/cache/openai-primary-runtime/presentations/26.1020.11127/skills/presentations'
const result=await finalizePresentation({
 workspaceDir:root,
 candidatePath:path.join(root,'.codex-ppt-build/slots/candidate.pptx'),
 finalPath:path.join(root,'diagram/AI智能客服-AF06-万佳恒-含抽槽填槽.pptx'),
 pythonExecutable:'/Users/wanjiaheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12',
 integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
 layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit'],
 explicitTotalSlideCount:7,
 requiredNativeTableOwnerSlides:[],requiredNativeChartOwnerSlides:[],
 fontPolicy:{basis:'design',families:['Heiti TC']},
 verifyArtifactToolImport:true,
 receiptPath:path.join(root,'.codex-ppt-build/slots/validation-final.json'),
})
console.log(JSON.stringify({ok:true,finalPath:result.finalPath,sha256:result.finalSha256,slides:result.packageIntegrity.slide_count,findings:result.presentationLayout.finding_count,warnings:result.presentationLayout.warnings}))
