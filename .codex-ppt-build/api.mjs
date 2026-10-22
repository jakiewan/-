import {FileBlob,PresentationFile} from '@oai/artifact-tool'
const p=await PresentationFile.importPptx(await FileBlob.load('diagram/z业务图/大模型开发学员答辩PPT模板（202602）.pptx'))
console.log('keep',p.slides.keep.toString().slice(0,1300),'reorder',p.slides.reorder.toString().slice(0,1000))
console.log('shapes methods',Object.getOwnPropertyNames(Object.getPrototypeOf(p.slides.items[0].shapes)))
console.log('remove',p.slides.items[0].shapes.remove.toString().slice(0,500))
console.log('style methods',Object.getOwnPropertyNames(Object.getPrototypeOf(p.slides.items[0].shapes.items[0].text)))
console.log('image remove',p.slides.items[0].images.remove.toString().slice(0,500))
