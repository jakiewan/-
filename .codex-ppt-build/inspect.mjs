import fs from 'node:fs/promises'
import {FileBlob,PresentationFile} from '@oai/artifact-tool'
const p=await PresentationFile.importPptx(await FileBlob.load('diagram/z业务图/大模型开发学员答辩PPT模板（202602）.pptx'))
console.log('slides',p.slides.items.length,'methods',Object.getOwnPropertyNames(Object.getPrototypeOf(p.slides)))
await fs.writeFile('.codex-ppt-build/reference/inspect.ndjson',(await p.inspect({kind:'slide,textbox,shape,image,layout',maxChars:100000})).ndjson)
for(let i=0;i<p.slides.items.length;i++){
 const s=p.slides.items[i]
 await fs.writeFile(`.codex-ppt-build/reference/slide-${i+1}.png`,new Uint8Array(await (await s.export({format:'png',scale:1})).arrayBuffer()))
 await fs.writeFile(`.codex-ppt-build/reference/slide-${i+1}.json`,await (await s.export({format:'layout'})).text())
 console.log('rendered',i+1)
}
