import fs from 'node:fs/promises'
import path from 'node:path'
import { Presentation, PresentationFile } from '@oai/artifact-tool'

const root = process.cwd()
const build = path.join(root, '.codex-ppt-build/rewrite')
const p = Presentation.create({ slideSize: { width: 1280, height: 720 } })
p.theme.defaultFont = 'Heiti TC'
const C = { ink:'#102B40', teal:'#087F8C', blue:'#285F98', muted:'#536A79', bg:'#F7F9FC', pale:'#E9F3F5', sky:'#EDF2FA', line:'#B6C8D6', amber:'#936421', sand:'#FFF6E7', gray:'#F0F2F4' }
function text(s, value, x,y,w,h,size=25,color=C.ink,bold=false,align='left') {
 const a=s.shapes.add({geometry:'textbox',name:value.split('\n')[0],position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}})
 a.text=value
 a.text.style={fontSize:size,typeface:'Heiti TC',color,bold,alignment:align,verticalAlignment:'middle',autoFit:'none',insets:0,wrap:'square'}
 return a
}
function node(s,value,x,y,w,h=64,opts={}) {
 const a=s.shapes.add({geometry:'rect',name:value.split('\n')[0],position:{left:x,top:y,width:w,height:h},fill:opts.fill||C.pale,line:{fill:opts.line||C.line,width:1.2,style:opts.dashed?'dashed':'solid'}})
 a.text=value
 a.text.style={fontSize:opts.size||23,typeface:'Heiti TC',color:opts.color||C.ink,bold:opts.bold||false,alignment:'center',verticalAlignment:'middle',autoFit:'none',insets:8,wrap:'square'}
 return a
}
function edge(s,a,b,from='right',to='left',opts={}) {
 return s.shapes.connect(a,b,{kind:opts.kind||'elbow',fromSide:from,toSide:to,line:{fill:opts.color||C.teal,width:1.8,style:opts.dashed?'dashed':'solid'},tail:{type:'triangle',width:'sm',length:'sm'}})
}
function line(s,x1,y1,x2,y2,color=C.line,arrow=false,dashed=false){
 return s.shapes.add({geometry:'connector',kind:'straight',position:{left:Math.min(x1,x2),top:Math.min(y1,y2),width:Math.abs(x2-x1),height:Math.abs(y2-y1),horizontalFlip:x2<x1,verticalFlip:y2<y1},line:{fill:color,width:1.6,style:dashed?'dashed':'solid'},tail:arrow?{type:'triangle',width:'sm',length:'sm'}:{type:'none'}})
}
function page(title,n,sub='') {
 const s=p.slides.add();s.background.fill=C.bg
 text(s,title,55,35,1130,58,42,C.ink,true)
 if(sub)text(s,sub,57,99,1150,37,23,C.muted)
 text(s,`AI智能客服  /  万佳恒`,55,682,750,22,17,C.muted)
 text(s,`${n} / 5`,1130,682,95,22,17,C.teal,false,'right')
 return s
}
{
 const s=page('解决什么问题',1,'面向电商售前与售后咨询，衔接知识问答、本人订单查询和售后申请')
 const rows=[
  ['重复咨询量大','参数、保修与售后政策分散，用户反复查找','FAQ 直接返回标准条款，复杂问法继续查文档'],
  ['多轮对话容易丢条件','“它的屏幕呢”“那保修多久”需要理解上下文','结合上一轮问答与待补槽位，重写完整检索问题'],
  ['回答与实际办理脱节','订单归属、申请商品和操作意愿需要核实','查询本人订单，确认退／换／修后生成本地申请']
 ]
 rows.forEach((r,i)=>{
  let y=170+i*130
  text(s,`0${i+1}`,57,y,65,45,33,C.teal,true)
  text(s,r[0],142,y,345,41,29,C.ink,true)
  text(s,r[1],142,y+48,1055,34,25,C.muted)
  text(s,r[2],640,y,555,42,25,C.teal,true)
  line(s,142,y+105,1225,y+105)
 })
 text(s,'项目边界：小米国行知识资料 + 本地演示订单／售后记录，人工转接仅展示等待文案',57,602,1166,55,24,C.amber)
 s.speakerNotes.text='本项目解决三个具体问题：重复查询资料、多轮对话缺少上下文、业务查询与售后动作脱节。用户先咨询参数或政策，再查询本人订单，需要实际办理时收集订单、商品、动作和原因，展示确认后落库。当前不连接外部商城，不代表真实退款或维修完成。\n源码依据：backend/graph/nodes.py:33-170、178-231、243-316；backend/graph/after_sales.py；backend/tools/after_sales.py；backend/data/knowledge/xiaomi-cn/README.md。'
}
{
 const s=page('系统架构',2,'Vue 3 提供对话界面，FastAPI 接入请求，LangGraph 编排业务与知识查询')
 const ui=node(s,'Vue 3 + Vite\n登录、对话、进度',55,168,245,80,{fill:'#FFFFFF'})
 const api=node(s,'FastAPI + SSE\nCookie 身份、请求排队',355,168,275,80,{fill:'#FFFFFF'})
 const graph=node(s,'LangGraph 客服主图\n规则与 LLM 路由、统一回复',685,168,540,80,{fill:C.sky,bold:true})
 edge(s,ui,api);edge(s,api,graph)
 const model=node(s,'LLM 接入\nDeepSeek、MiniMax\n分类、重写与生成',55,340,245,117,{fill:'#FFFFFF',size:22})
 const rag=node(s,'固定 RAG 子流程\nFAQ、文档混合检索、精排\n可选手机参数关系查询',355,340,395,117,{size:24})
 const biz=node(s,'业务节点\n本人订单查询\n退货／换货／维修申请',805,340,420,117,{fill:C.sky,size:24})
 edge(s,graph,rag,'bottom','top');edge(s,graph,biz,'bottom','top')
 edge(s,api,model,'bottom','top',{dashed:true,color:C.blue})
 text(s,'模型供 Graph 与 RAG 使用',55,475,285,34,20,C.muted)
 const mv=node(s,'Milvus\nFAQ 与文档向量',355,548,205,74,{fill:'#FFFFFF'})
 const rd=node(s,'Redis\n版本化正文、目录',585,548,215,74,{fill:'#FFFFFF'})
 const pg=node(s,'PostgreSQL\n会话检查点、订单、申请',835,548,390,74,{fill:'#FFFFFF'})
 edge(s,rag,mv,'bottom','top');edge(s,rag,rd,'bottom','top');edge(s,biz,pg,'bottom','top')
 const neo=node(s,'Neo4j（可选）\n手机参数关系',55,548,245,74,{fill:'#FFFFFF',size:22})
 line(s,385,457,385,520,C.teal);line(s,385,520,177,520,C.teal);line(s,177,520,177,548,C.teal,true)
 text(s,'原始 Markdown 统一维护知识，更新 Milvus / Redis；参数关系单独同步 Neo4j',355,634,870,33,20,C.muted)
 s.speakerNotes.text='一次请求由浏览器发往 FastAPI，服务通过 Cookie 解析当前账号，用用户级 thread_id 与 PostgreSQL advisory lock 串行处理同一会话。MiniMaxClient/DeepSeekClient 调用 build_workflow。咨询进入固定 RAG 子流程，订单与售后节点直接读写 PostgreSQL。Milvus 保存向量索引，Redis 保存正文和版本元数据；启用 RAG_NEO4J_URI 后，手机参数关系查询可读取 Neo4j。PostgreSQL 也保存主图检查点及 Store；登录会话本身当前仍保存在单进程内存，不是 JWT。\n知识维护：backend/data/knowledge/xiaomi-cn/原始资料/ 为当前小米资料唯一日常入口，rebuild_knowledge.py 更新文档与 FAQ；sync_phone_graph.py 同步专用手机图谱。\n源码依据：backend/main.py:26-77、86-160；backend/common/auth.py；backend/utils/minimax_client.py:16-65；backend/utils/deepseek_client.py；backend/rag/retriever.py:29-86；backend/scripts/rebuild_knowledge.py；backend/scripts/sync_phone_graph.py。'
}
{
 const s=page('项目亮点',3,'围绕实际问答与办理过程，保持流程可追踪、证据有来源、写操作有确认')
 const rows=[
  ['可控的单意图路由','明确寒暄与转人工走 Python 规则，其余由 LLM 分类','多轮上下文与 SSE 节点进度可追踪'],
  ['分层检索与独立精排','FAQ 优先，未明确命中再走原查询 + HyDE 混合检索','双向量召回后，按证据相关性独立精排'],
  ['参数关系查询有确定结果','Neo4j 支持型号比较、共同参数与多条件筛选','固定 Cypher 查询，程序计算差值与筛选结果'],
  ['售后确认与重复提交保护','核对本人订单、商品和动作，明确确认后再创建申请','参数改变重新确认，同一申请不重复创建']
 ]
 rows.forEach((r,i)=>{
  let y=160+i*116
  text(s,`0${i+1}`,55,y,66,45,31,C.teal,true)
  text(s,r[0],140,y,440,40,27,C.ink,true)
  text(s,r[1],140,y+47,1080,34,24,C.ink)
  text(s,r[2],640,y,575,42,23,C.teal)
  line(s,140,y+98,1225,y+98)
 })
 text(s,'知识来自同一份 Markdown 正文，内部保留来源与版本；未找到依据时明确说明无法核实',57,638,1165,32,22,C.muted)
 s.speakerNotes.text='亮点对应当前代码行为，不将方案或离线实验写成已上线能力。当前 recognize_intent 未使用 BertIntentClassifier，intent_context_v3.csv 等文件属于保留的离线训练资料，因此不展示 BERT 准确率。固定 RAG 也没有检索规划 Agent、证据选择 Agent 或重查循环。FAQ 直接返回正文条款，文档回答依证据生成，HyDE 只用于扩展查询，不能充当证据。Neo4j 为可选扩展，在线只读并检查源文件版本。售后在提交前再次验证本人订单及商品快照，申请仅代表本地待审核记录。validate_answer 目前检查非空和部分确定性边界，不是完整的语义事实校验。\n源码依据：backend/graph/nodes.py:23-98、243-316；backend/rag/agent.py；backend/rag/models.py:53-108；backend/rag/phone_graph.py；backend/graph/after_sales.py；backend/tools/after_sales.py。'
}
{
 const s=page('客服 Graph 主流程',4,'单意图分流后汇总业务结果，再生成回复并校验')
 const start=node(s,'START',55,156,120,54,{fill:'#FFFFFF',size:22})
 const prep=node(s,'prepare_context\n保留有效槽位',215,151,220,64,{size:22})
 const intent=node(s,'recognize_intent\n规则 + LLM 分类',475,151,260,64,{fill:C.sky,bold:true,size:22})
 edge(s,start,prep);edge(s,prep,intent)
 text(s,'复杂子图保留但当前不进入\nis_complex=False，业务工具未接入',795,149,430,68,21,C.muted)
 const b=[
  node(s,'咨询\n知识检索',55,294,170,70),
  node(s,'订单查询\n本人订单',250,294,170,70,{size:21}),
  node(s,'售后\n确认后提交',445,294,170,70,{size:21}),
  node(s,'闲聊\n规则或普通回复',640,294,170,70,{size:21}),
  node(s,'投诉\n标记需要人工',835,294,170,70,{fill:C.sand,size:21}),
  node(s,'转人工',1030,294,195,70,{fill:C.sand,size:23}),
 ]
 b.forEach(a=>edge(s,intent,a,'bottom','top'))
 const agg=node(s,'aggregate_results  汇总结果、证据与动作',55,404,950,46,{fill:C.sky,size:22})
 b.slice(0,5).forEach(a=>edge(s,a,agg,'bottom','top'))
 const check=node(s,'check_handoff\n检查人工需求',445,476,220,60,{size:22})
 const hand=node(s,'handoff\n展示转接等待文案',1030,476,195,60,{fill:C.sand,size:21})
 edge(s,agg,check,'bottom','top');edge(s,check,hand,'right','left')
 edge(s,b[5],hand,'bottom','top')
 text(s,'需要人工',760,477,150,32,19,C.amber,false,'center')
 const ans=node(s,'generate_answer\n生成或直接返回回复',445,566,220,60,{fill:C.sky,size:21})
 edge(s,check,ans,'bottom','top')
 text(s,'否',572,539,24,25,18,C.muted)
 line(s,1127,536,1127,549,C.teal);line(s,1127,549,620,549,C.teal);line(s,620,549,620,566,C.teal,true)
 const val=node(s,'validate_answer\n非空与边界校验',760,566,220,60,{size:21})
 const end=node(s,'END',1095,566,130,60,{fill:'#FFFFFF',size:24})
 edge(s,ans,val);edge(s,val,end)
 text(s,'通过',1003,562,70,30,19,C.teal,false,'center')
 const fallback=node(s,'safe_fallback',760,641,220,30,{fill:C.sand,size:19})
 edge(s,val,fallback,'bottom','top');edge(s,fallback,end,'right','bottom')
 text(s,'失败',680,639,68,29,19,C.amber)
 text(s,'需澄清直接生成追问\n订单判为咨询时回到知识检索',55,499,350,78,21,C.muted)
 s.speakerNotes.text='主流程：START → prepare_context → recognize_intent。咨询、订单、售后、闲聊、投诉进入各业务节点，随后 aggregate_results → check_handoff；人工需求先执行 handoff，否则直接 generate_answer。显式转人工从识别节点直接走 handoff。needs_clarification=true 时跳过业务节点直接生成追问。query_order 如果识别到用户实际在问商品信息，会改走 retrieve_knowledge。generate_answer → validate_answer，通过结束，失败走 safe_fallback → END；不重新提交业务。\n当前复杂子图仍保留 agent_decide → execute_tools → agent_decide 的结构，以及工具上限与结果收集节点；但 recognize_intent 固定 is_complex=False，agent_decide 也不要求工具，因此这不是已启用的自主工具 Agent。投诉只标记人工需求，handoff 返回 demo_waiting 文案，不代表真实坐席接管。\n此页为便于答辩阅读合并了部分节点显示名；精确条件边依据 backend/graph/workflow.py:23-139，节点行为依据 backend/graph/nodes.py。'
}
{
 const s=page('RAG 检索流程',5,'固定顺序查询，返回真实证据；FAQ 与图谱命中时跳过文档生成')
 const rewrite=node(s,'问题重写\n补全上下文与条件',55,185,245,85,{fill:C.sky,size:23})
 const graph=node(s,'参数关系查询（可选）\nNeo4j 固定 Cypher',355,185,300,85,{size:22})
 const faq=node(s,'FAQ 问法召回\n相关性校验与去重',710,185,245,85,{size:22})
 const direct=node(s,'直接返回\n图谱表格／FAQ',1010,185,215,85,{fill:C.sky,size:22})
 edge(s,rewrite,graph);edge(s,graph,faq);edge(s,faq,direct)
 text(s,'普通问题直接查 FAQ\n图谱未覆盖、版本过期或异常时回到 FAQ',55,285,680,67,23,C.muted)
 // 图谱命中通过独立的上方路径，绕过 FAQ。
 line(s,505,185,505,153,C.teal);line(s,505,153,1117,153,C.teal);line(s,1117,153,1117,185,C.teal,true)
 text(s,'图谱命中',727,118,200,31,20,C.teal,false,'center')
 text(s,'明确命中',973,277,230,31,20,C.teal,false,'center')
 const orig=node(s,'原查询混合检索\nBGE-M3\nDense + Sparse',55,409,245,85,{size:22})
 const hyde=node(s,'HyDE 生成\n假设文本仅用于检索',355,409,230,85,{size:22})
 const hs=node(s,'HyDE 混合检索\nBGE-M3\nDense + Sparse',640,409,245,85,{size:22})
 const rrf=node(s,'RRF 融合去重\n合并两次真实文档召回',940,409,285,85,{fill:C.sky,size:22})
 edge(s,orig,hyde);edge(s,hyde,hs);edge(s,hs,rrf)
 line(s,832,270,832,361,C.teal);line(s,832,361,177,361,C.teal);line(s,177,361,177,409,C.teal,true)
 text(s,'FAQ 未明确命中',390,337,300,29,21,C.teal,false,'center')
 const rerank=node(s,'独立 Reranker 精排\n最多 5 条、正文 8000 字符',940,551,285,82,{fill:C.sky,size:22})
 const result=node(s,'整理真实证据\n保留来源与版本',640,551,245,82,{size:22})
 const answer=node(s,'主图统一回复\n依据真实证据生成',355,551,230,82,{size:22})
 edge(s,rrf,rerank,'bottom','top');edge(s,rerank,result,'left','right');edge(s,result,answer,'left','right')
 text(s,'原查询 / HyDE 权重 1.0 / 0.7，RRF k=60',55,645,735,30,21,C.muted)
 text(s,'缺条件先澄清，缺证据说明无法核实',805,645,420,30,21,C.amber)
 s.speakerNotes.text='先用当前消息、上一轮完整问答与待补任务重写问题，提取品类、品牌、型号、地区和渠道。缺少咨询方面、型号等必要条件时返回澄清；海外版或不支持品牌明确说明边界。\n全部诉求能表达为手机参数关系时可查 Neo4j。命中直接返回程序形成的表格；未配置、未覆盖、源文档版本不一致或连接异常时回到 FAQ/文档链路。普通问题直接查 FAQ。FAQ 使用 BGE-M3 dense 召回，Reranker 校验后按条目去重；型号参数问题不会套用泛品类政策 FAQ。明确命中直接输出标准条款。\nFAQ no_match 或 ambiguous 才进入文档检索。先对原查询做 dense/sparse 混合检索，再生成 HyDE 假设文本并做第二次混合检索。代码为顺序执行，不是并行 Agent。两次检索结果都来自真实文档，HyDE 本身不能作为证据。每次 dense/sparse 默认各召回20条，权重各0.5，混合后保留10条。RRF k=60，原查询/HyDE权重1.0/0.7，按带版本的 evidence_id 去重，融合最多10条。随后独立 bge-reranker-base 精排，最多5条、8000字符，允许0条；多个型号分配证据预算。\nrag_result 返回业务结果与证据给主图，由 generate_answer 依正文回答。无匹配说明无法核实，服务异常明确失败。FAQ/图谱直接答案同样经过主图回复出口。图中的直接返回指跳过文档生成，不指跳过主图。\n源码依据：backend/rag/agent.py:49-373；backend/rag/retriever.py:89-220；backend/rag/models.py:18-108；backend/rag/phone_graph.py；backend/graph/nodes.py:243-316。'
}
if(p.slides.items.length!==5)throw new Error('Expected exactly five slides')
for(let i=0;i<5;i++){
 const s=p.slides.items[i]
 await fs.writeFile(path.join(build,`slide-${i+1}.png`),new Uint8Array(await (await s.export({format:'png',scale:1.2})).arrayBuffer()))
 await fs.writeFile(path.join(build,`slide-${i+1}.layout.json`),await (await s.export({format:'layout'})).text())
 console.log('rendered',i+1)
}
await (await PresentationFile.exportPptx(p)).save(path.join(build,'candidate.pptx'))
await fs.writeFile(path.join(build,'inspect.ndjson'),(await p.inspect({kind:'slide,textbox,shape,notes',maxChars:100000})).ndjson)
console.log('candidate ready')
