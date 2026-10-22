import {FileBlob,PresentationFile} from '@oai/artifact-tool'
const p=await PresentationFile.importPptx(await FileBlob.load('diagram/z业务图/大模型开发学员答辩PPT模板（202602）.pptx'))
console.log('image methods',Object.getOwnPropertyNames(Object.getPrototypeOf(p.slides.items[0].images)))
console.log('text style',p.slides.items[0].shapes.items[0].text.style)
console.log('del',p.slides.items[0].shapes.deleteById.toString().slice(0,500))
console.log('image delete',p.slides.items[0].images.deleteById?.toString().slice(0,500))
