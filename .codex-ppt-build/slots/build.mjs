import fs from 'node:fs/promises'
import {Presentation,PresentationFile} from '@oai/artifact-tool'
const p=Presentation.create({slideSize:{width:1280,height:720}})
p.theme.defaultFont='Heiti TC'
const s=p.slides.add();s.background.fill='#F7F9FC'
function t(v,x,y,w,h,size=25,color='#102B40',bold=false){const a=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});a.text=v;a.text.style={fontSize:size,typeface:'Heiti TC',color,bold,verticalAlignment:'middle',autoFit:'none',insets:0,wrap:'square'};return a}
t('抽槽与跨轮补参',55,35,1130,58,42,'#102B40',true)
t('LLM 提取本轮条件，Python 保留有效槽位，缺少信息时继续追问',57,99,1150,37,23,'#536A79')
const rows=[['01','抽取参数','LLM 输出 JSON，Pydantic 校验字段结构'],['02','合并与追问','沿用已知条件，补充或纠正新值，缺参继续问'],['03','核实与确认','订单归属由数据库核实，售后明确确认后提交']]
rows.forEach((r,i)=>{const y=170+i*100;t(r[0],57,y,65,45,31,'#087F8C',true);t(r[1],142,y,285,42,28,'#102B40',true);t(r[2],430,y,790,45,25)})
t('咨询示例',57,490,220,40,27,'#087F8C',true)
t('“我想了解小米15”  →  记录型号，追问咨询方面',57,540,1165,40,25)
t('“屏幕参数”  →  继承型号，检索“小米15的屏幕参数”',57,585,1165,40,25)
t('咨询与售后支持跨轮补参，订单查询仅提取本轮条件',57,638,1165,32,22,'#536A79')
t('AI智能客服  /  万佳恒',55,682,750,22,17,'#536A79');t('5 / 7',1130,682,95,22,17,'#087F8C')
s.speakerNotes.text='抽槽是从用户消息中提取业务参数，填槽是合并已知参数并追问缺失条件。咨询在 rag_rewrite 中结合本轮消息、上一轮问答与待补任务提取条件，continues_task 为 true 时继承品类、型号、渠道和品牌。售后仅抽取新增字段，Python 合并非空值，参数变化后重新确认。订单查询只提取本轮条件。Pydantic 校验结构，不代表语义正确；数据库核实本人订单，售后还需明确确认。例子为代码流程说明，不代表本次接口实测。源码：backend/rag/agent.py:20,53,109,238,324；backend/graph/after_sales.py:23,125,197；backend/graph/nodes.py:162。'
await (await PresentationFile.exportPptx(p)).save('.codex-ppt-build/slots/one-slide.pptx')
await fs.writeFile('.codex-ppt-build/slots/layout.json',await (await s.export({format:'layout'})).text())
console.log('created editable slide')
