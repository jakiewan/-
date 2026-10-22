import fs from 'node:fs/promises'
import {FileBlob,PresentationFile} from '@oai/artifact-tool'
const p=await PresentationFile.importPptx(await FileBlob.load('diagram/AI智能客服-AF06-万佳恒.pptx'))
console.log('slides',p.slides.items.length)
for(let i=0;i<p.slides.items.length;i++){
 const b=await p.slides.items[i].export({format:'png',scale:0.65})
 await fs.writeFile(`.codex-ppt-build/rewrite/source-${i+1}.png`,new Uint8Array(await b.arrayBuffer()))
}
await fs.writeFile('.codex-ppt-build/rewrite/source-inspect.ndjson',(await p.inspect({kind:'slide,textbox,shape',maxChars:30000})).ndjson)
console.log('rendered all source slides')
