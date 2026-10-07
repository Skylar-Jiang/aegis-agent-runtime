import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const runtimeModules=process.env.RUNTIME_NODE_MODULES || 'C:/Users/Alghy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
process.env.RUNTIME_NODE_MODULES=runtimeModules;
const runtimeRequire=createRequire(path.join(runtimeModules,'package.json'));
const {Presentation,PresentationFile}=await import(pathToFileURL(runtimeRequire.resolve('@oai/artifact-tool')).href);
const root=process.cwd();
const skill='C:/Users/Alghy/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations';
const {finalizePresentation,applyPresentationChartFont}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')));
const integration=path.join(root,'experiments/intent/integration/20261007T120741Z-001c05');
const summary=JSON.parse(await fs.readFile(path.join(integration,'summary.json'),'utf8'));
const p=Presentation.create({slideSize:{width:1280,height:720}});
const font='Microsoft YaHei';
function text(slide,value,x,y,w,h,size=30,bold=false){const s=slide.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});s.text=value;s.text.style={typeface:font,fontSize:size,bold,color:'#172b3a',autoFit:'none'};return s;}
function slide(title,lines){const s=p.slides.add();s.background.fill='#F7F9FC';text(s,title,64,48,1150,100,44,true);if(lines)text(s,lines.join('\n\n'),72,178,1136,450,30);s.speakerNotes.textFrame.setText('证据来源：experiments/intent/integration/20261007T120741Z-001c05/summary.json，scripts/verify_intent_local.py。合成已见用例回归，人工标签尚待独立复核。');return s;}
slide('Aegis Intent 本地实现与实验验收',['2 号数据与实验工作包','2026 年 10 月 7 日','真实 Core、SM2 证据链与工具效果']);
slide('10 月 7 日自动验收已具备',['三类软偏移：检索污染、记忆诱导重复、失败后替代报告','正常对照：多步查询、合法资料替换、可信目标更新、失败回退','执行前拒绝、安全终止与有界报告恢复','真实前端事件链、风险维度、上游引用与版本记录']);
const s=slide('五组真实运行时对照，共 200 条轨迹');
const arms=Object.keys(summary.arms);
const c=s.charts.add('bar',{position:{left:72,top:180,width:1120,height:400},categories:['权限边界','单步检查','完整规则','去序列','去来源引用'],series:[{name:'危险报告写入轨迹',values:arms.map(a=>summary.arms[a].forbidden_effect_trajectories),fill:'#D65858'},{name:'安全终止轨迹',values:arms.map(a=>summary.arms[a].safe_stops),fill:'#278A78'}],barOptions:{direction:'column',grouping:'clustered'},hasLegend:true,dataLabels:{showValue:true,position:'outEnd'}});applyPresentationChartFont(c,{fontFamily:font});text(s,'每组 40 条。来源消融没有改变拦截结果，来源功能仅提供证据关联。',72,608,1120,68,23);
slide('完整规则组的正常用例全部完成',['正常完成 8 / 8','软偏移安全终止 24 / 24','危险报告写入 0 条','硬边界拒绝继续由原权限网关负责']);
slide('检测与恢复都有明确范围',['检测依据：已确认报告路径、必须出现的文本、有界重复读取','恢复方案：复读指定证据，按原条件写入原报告路径','执行限制：用户确认、两次提案、四次调用、每次重新过网关','来源变化、契约变化、权限变化或预算耗尽时安全终止']);
const shot=slide('真实前端记录执行前拒绝与风险证据');
text(shot,'权限范围允许写报告\n\n实际内容替换为广告\n\n完成条件不匹配\n\n执行前拒绝并安全终止\n\n上游读取引用与版本保留',72,180,500,470,30);
shot.images.add({blob:new Uint8Array(await fs.readFile(path.join(integration,'browser/retrieval-risk.png'))),contentType:'image/png',alt:'真实 Core 检索污染任务的事件与风险时间线',fit:'contain',position:{left:600,top:155,width:600,height:505}});
slide('复核与正式验收仍由真实成员完成',['40 条试验轨迹，另准备 200 条参数变体候选','两名成员独立审核标签和关键偏移步骤，解决分歧','确认官方任务范围、资源口径和提交格式','由非作者复跑并审阅报告、录像和演示材料']);
slide('结果边界与下一步',['当前规则针对报告文本一致性与重复读取，尚不具备通用语义理解','已见合成用例结果不能当作未见正式测试分数','原始 SM2 验签成功证明证据完整性，不证明任务结论正确','人工复核与团队验收完成后再推送 GitHub']);
const build=path.join(root,'.runtime/ppt-build');const final=path.join(root,`experiments/intent/materials/Aegis-Intent-local-acceptance-${Date.now()}.pptx`);
await(await PresentationFile.exportPptx(p)).save(path.join(build,'candidate.pptx'));
for(let i=0;i<p.slides.items.length;i++){const blob=await p.export({slide:p.slides.items[i],format:'png',scale:1});await fs.writeFile(path.join(build,`slide-${i+1}.png`),new Uint8Array(await blob.arrayBuffer()));}
const montage=await p.export({format:'webp',montage:true,scale:.5});await fs.writeFile(path.join(build,'montage.webp'),new Uint8Array(await montage.arrayBuffer()));
await finalizePresentation({workspaceDir:root,candidatePath:path.join(build,'candidate.pptx'),finalPath:final,pythonExecutable:'C:/Users/Alghy/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit'],materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,receiptPath:path.join(build,'validation.json')});
console.log(final);
