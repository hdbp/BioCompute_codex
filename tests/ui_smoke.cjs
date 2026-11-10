/* Static UI rendering smoke test without a browser/CDN dependency. */
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync('frontend/app.js','utf8');
const context={
  document:{querySelector:(sel)=>sel==='#app'?{innerHTML:''}:null,
    documentElement:{classList:{toggle:()=>{}}}, addEventListener:()=>{}},
  sessionStorage:{getItem:()=>'',setItem:()=>{},removeItem:()=>{}},
  localStorage:{getItem:()=>'',setItem:()=>{}},
  setInterval:()=>0,setTimeout:()=>0,
  Intl,URL,Math,Date,Number,JSON,String,Object,Array,console,structuredClone,
};
const tests=String.raw`
appState.token='smoke-test';appState.initialized=true;
appState.system={hostname:'lab-controller',version:'0.2.0',nextflow:false,htcondor:false,
  tailscale:false,data_roots:['/tmp/data']};
appState.nodes=[{id:'local-controller',hostname:'lab-controller',source:'local',os:'Linux',arch:'x86_64',
  cpu_total:32,ram_total:64*1024**3,details:{cpu_percent:15,ram_used:16*1024**3},
  enabled:true,authorized:true,can_execute_now:true,cpu_limit:28,status:'online'}];
appState.pipelines=[{id:'builtin-fastq-qc',name:'FASTQ Quality Explorer',engine:'builtin',
  description:'Real QC',category:'Quality control',fields:[{name:'max_reads',title:'Max reads',
  type:'integer',group:'General',default:100000}],sample_columns:[]}];
appState.data={nodes:{discovered:1,authorized:1,active:1,total_known_cpu:32},jobs:{},
  controller:appState.nodes[0],recent_runs:[],pipelines:1,experiments:0};
const rendered={};
for(const route of ['dashboard','nodes','pipelines','experiments','studio','runs','settings']){
 appState.route=route;const content=renderPage();
 if(typeof content!=='string'||content.length<200)throw Error('Render failed: '+route);
 rendered[route]=content.length;
}
for(const step of [0,1,2,3,4]){
 appState.wizard={mode:'run',step,pipelineId:'builtin-fastq-qc',name:'Smoke test',
  params:{max_reads:100000},samples:[{sample:'A',condition:'Control',replicate:1,batch:'',
  file1:'/tmp/data/A.fastq',file2:'',metadata:{}}],validation:null};
 if(!renderWizard().includes('drawer'))throw Error('Wizard step failed '+step);
}
appState.wizard={mode:'experiment',name:'Exp test',assay:'Genomics',
  samples:[],params:{},validation:null,pipelineId:'builtin-fastq-qc'};
if(!renderWizard().includes('EXPERIMENT DESIGNER'))throw Error('Experiment editor failed');
appState.wizard=null;
appState.runDetail={id:'abc',name:'QC run',status:'running',samples:[],created_at:null,
 pipeline_id:'builtin-fastq-qc'};
if(!renderRunDetail().includes('Execution log'))throw Error('Run details failed');
appState.picker={field:'file1',row:0,list:{path:null,roots:[{name:'demo',path:'/data'}],entries:[]}};
if(!renderPicker().includes('Choose'))throw Error('File picker failed');
appState.modal={type:'import'};
if(!renderModal().includes('Import Nextflow'))throw Error('Import modal failed');
console.log('UI smoke rendering passed:',JSON.stringify(rendered));
`;
vm.runInNewContext(source+'\n'+tests,context,{filename:'frontend/app.js'});
