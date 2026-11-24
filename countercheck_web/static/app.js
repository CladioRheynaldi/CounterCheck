'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=name=>`<svg class="icon" aria-hidden="true"><use href="#${name}"/></svg>`;
let menu={},items=[],raw=[],warnings=[],decisions=[],receipts=[],metrics=[],browserLatencies=[],corrections=[];
let running=false,confirmed=false,manual=false,lost=false,pending=false,ready=false,socket,media,context,capture,source;
let level=0,selectedModel='baseline',currentEpoch=0,awaitingNewOrder=false;
let editing=false,editIndex=-1,editDraft=null,deferredOrder=null,edited=new Set(),activeCombo=null;

function alertText(text){$('alert').textContent=text;$('alert').hidden=!text;}
function note(text){$('manual-note').textContent=text;$('manual-note').hidden=!text;}
function controls(){
  $('start').disabled=running||!ready;$('stop').disabled=!running;$('model').disabled=running||!ready;
  $('reviewed').disabled=confirmed;$('add').disabled=confirmed||!ready;$('unlock').hidden=!confirmed;
  $('confirm').disabled=confirmed||lost||pending||editing||!$('reviewed').checked||!items.length||items.some(i=>!i.item||!Number.isInteger(i.quantity)||i.quantity<1||i.quantity>20);
  $('confirm-label').textContent=confirmed?'Order confirmed':'Confirm order';
  $('capture-state').textContent=running?'Listening continuously':'Microphone off';
  $('capture-hint').textContent=running?'Speak naturally. Pause briefly to update the order.':'Start listening, then speak an order naturally.';
  document.querySelector('.mic-panel').classList.toggle('listening',running);
  $('order-state').textContent=confirmed?'✓ Confirmed':manual?'Edited draft':'Draft';
  $('order-state').className='badge '+(confirmed?'confirmed':manual?'edited':'draft');
}
function renderItems(){
  const query=$('order-search').value.trim().toLowerCase();
  const tokens=query.split(/\s+/).filter(Boolean);
  const rows=items.map((item,index)=>({item,index})).filter(({item})=>{
    const haystack=[item.item,item.quantity,item.size,item.temperature,item.milk,...(item.modifiers||[])].join(' ').toLowerCase();
    return tokens.every(token=>haystack.includes(token));
  });
  $('item-count').textContent=`${items.length} ${items.length===1?'item':'items'}`;
  $('clear-search').hidden=!query;$('search-count').textContent=query?`${rows.length} of ${items.length} items`:'';
  if(!rows.length){
    $('items').innerHTML=query?'<tr class="empty-row"><td class="no-results" colspan="8">No matching order items. Try another search or clear the filter.</td></tr>':`<tr class="empty-row"><td colspan="8"><div class="empty-state">${icon('cup-icon')}<h3>No items yet</h3><p>Start the microphone or add an item manually.</p></div></td></tr>`;
    controls();return;
  }
  $('items').innerHTML=rows.map(({item,index})=>{
    const status=confirmed?'<span class="badge confirmed">✓ Confirmed</span>':edited.has(index)?'<span class="badge edited">Edited</span>':item.quantity==null?'<span class="badge draft">Needs quantity</span>':decisions.length?'<span class="badge inferred">Review match</span>':'<span class="badge neutral">Review</span>';
    return `<tr class="order-item" data-index="${index}"><td data-column="drink" data-label="Drink">${esc(item.item||'Unknown drink')}</td><td data-column="quantity" data-label="Quantity">${item.quantity??'?'}</td><td data-column="size" data-label="Size">${esc(item.size||'Unspecified')}</td><td data-column="temperature" data-label="Temperature">${esc(item.temperature||'Unspecified')}</td><td data-column="milk" data-label="Milk">${esc(item.milk||'Unspecified')}</td><td data-column="extras" data-label="Extras">${esc((item.modifiers||[]).join(', ')||'—')}</td><td data-label="Status">${status}</td><td><button type="button" class="edit-button" aria-label="Edit ${esc(item.item||'order')} item ${index+1}"${confirmed?' disabled':''}>${icon('edit-icon')}Edit</button></td></tr>`;
  }).join('');
  $('items').querySelectorAll('.edit-button').forEach(button=>button.addEventListener('click',()=>openEditor(Number(button.closest('tr').dataset.index))));
  controls();
}
function renderNotes(){
  $('warnings').replaceChildren();const unique=[...new Set(warnings)];
  if(unique.length){
    const main=document.createElement('p');main.textContent=unique[0];$('warnings').append(main);
    if(unique.length>1){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=`${unique.length-1} more review notes`;details.append(summary);for(const text of unique.slice(1)){const p=document.createElement('p');p.textContent=text;details.append(p);}$('warnings').append(details);}
  }
  $('decision-count').textContent=decisions.length?`${decisions.length} ${decisions.length===1?'match':'matches'}`:'';
  $('choice-list').replaceChildren();if(!decisions.length)$('choice-list').textContent='No menu corrections yet.';
  for(const decision of decisions){const group=document.createElement('div');const head=document.createElement('p');head.textContent=`${decision.category}: “${decision.original}” → ${decision.proposed} · ${decision.status}`;group.append(head);
    for(const c of decision.candidates){const p=document.createElement('p');p.textContent=`${c.value}: match ${c.score.toFixed(3)}${c.audio_score!=null?` · audio ${c.audio_score.toFixed(3)} · similarity ${c.similarity.toFixed(2)}`:''}`;group.append(p);}
    const p=document.createElement('p');p.textContent='Relative ranking, not a confidence percentage. Edit a wrong guess.';group.append(p);$('choice-list').append(group);
  }
}
function renderTranscript(){
  $('transcript').replaceChildren();if(!raw.length){const span=document.createElement('span');span.className='empty';span.textContent="Your customer's words will appear here.";$('transcript').append(span);}
  else for(const text of raw){const div=document.createElement('div');div.textContent=text;$('transcript').append(div);}
  $('transcript').scrollTop=$('transcript').scrollHeight;
}
function closeCombos(){for(const combo of document.querySelectorAll('.menu-combo')){combo.querySelector('.menu-options').hidden=true;combo.querySelector('input').setAttribute('aria-expanded','false');combo.querySelector('input').removeAttribute('aria-activedescendant');}activeCombo=null;}
function showOptions(category,query=''){
  closeCombos();const combo=document.querySelector(`.menu-combo[data-category="${category}"]`),input=combo.querySelector('input'),list=combo.querySelector('.menu-options');
  const choices=(category==='drink'?[]:[null]).concat(menu[category]).filter(value=>(value||'Unspecified').toLowerCase().includes(query.trim().toLowerCase()));
  list.replaceChildren();list.hidden=false;input.setAttribute('aria-expanded','true');activeCombo={category,choices,index:-1};
  if(!choices.length){const p=document.createElement('p');p.textContent='No matching options. Try another search.';list.append(p);}
  choices.forEach((value,index)=>{const button=document.createElement('button');button.type='button';button.setAttribute('role','option');button.setAttribute('aria-selected','false');button.dataset.value=value||'';button.textContent=value||'Unspecified';button.id=`${category}-option-${index}`;button.tabIndex=-1;
    button.addEventListener('pointerdown',event=>event.preventDefault());button.addEventListener('click',()=>chooseOption(category,value));list.append(button);});
}
function chooseOption(category,value){editDraft[category==='drink'?'item':category]=value;document.querySelector(`#edit-${category}`).value=value||'';closeCombos();$('editor-error').hidden=true;}
function renderExtras(){
  const query=$('extras-search').value.trim().toLowerCase();const matches=menu.extras.filter(value=>value.includes(query));
  $('editor-extras').innerHTML=matches.length?matches.map(value=>`<label><input type="checkbox" data-extra="${esc(value)}"${editDraft.modifiers.includes(value)?' checked':''}>${esc(value)}</label>`).join(''):'<span class="muted">No matching extras.</span>';
  $('editor-extras').querySelectorAll('input').forEach(input=>input.addEventListener('change',()=>{const set=new Set(editDraft.modifiers);input.checked?set.add(input.dataset.extra):set.delete(input.dataset.extra);editDraft.modifiers=[...set];}));
}
function openEditor(index){
  if(confirmed||!ready)return;
  editIndex=index;editDraft=structuredClone(index<0?{item:null,quantity:1,size:null,temperature:null,milk:null,modifiers:[]}:items[index]);
  editDraft.modifiers=editDraft.modifiers||[];editing=true;deferredOrder=null;
  $('editor-title').textContent=index<0?'Add an item':'Correct item';$('save-edit').innerHTML=icon('check-icon')+(index<0?'Add to order':'Save correction');
  $('edit-quantity').value=editDraft.quantity??'';$('editor-error').hidden=true;$('remove-item').hidden=index<0;$('extras-search').value='';
  $('editor-menu-fields').innerHTML=['drink','size','temperature','milk'].map(category=>`<div class="menu-combo" data-category="${category}"><label for="edit-${category}">${category[0].toUpperCase()+category.slice(1)}</label><div class="menu-input-wrap"><input id="edit-${category}" data-field="${category==='drink'?'item':category}" type="search" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${category}-options" placeholder="Search ${category}…" value="${esc(editDraft[category==='drink'?'item':category]||'')}"></div><div id="${category}-options" class="menu-options" role="listbox" hidden></div></div>`).join('');
  for(const category of ['drink','size','temperature','milk']){
    const input=$(`edit-${category}`);
    input.addEventListener('focus',()=>{input.select();showOptions(category);});
    input.addEventListener('input',()=>showOptions(category,input.value));
    input.addEventListener('keydown',event=>{
      if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();if(!activeCombo||activeCombo.category!==category)showOptions(category,input.value);const state=activeCombo;if(!state.choices.length)return;state.index=(state.index+(event.key==='ArrowDown'?1:-1)+state.choices.length)%state.choices.length;const list=input.closest('.menu-combo').querySelector('.menu-options');for(const [i,button] of [...list.querySelectorAll('button')].entries()){button.classList.toggle('active',i===state.index);button.setAttribute('aria-selected',String(i===state.index));}input.setAttribute('aria-activedescendant',`${category}-option-${state.index}`);list.querySelector('.active')?.scrollIntoView({block:'nearest'});}
      else if(event.key==='Enter'&&activeCombo?.category===category){event.preventDefault();if(activeCombo.choices.length)chooseOption(category,activeCombo.choices[Math.max(0,activeCombo.index)]);}
      else if(event.key==='Escape'&&activeCombo){event.preventDefault();event.stopPropagation();closeCombos();}
    });
  }
  renderExtras();$('editor').showModal();controls();
  if(index<0)$('edit-drink').focus();else $('edit-quantity').focus();
}
function applyOrder(order){
  if(!manual){items=order.items;edited.clear();}
  else note('Manual corrections are protected. Check new speech against the live transcript.');
  $('interpreted').textContent='Menu interpretation: '+order.interpreted_text;warnings=order.warnings;decisions.push(...(order.menu_decisions||[]));decisions=decisions.slice(-1000);$('reviewed').checked=false;renderItems();renderNotes();
}
function closeEditor(saved=false){
  closeCombos();$('editor').close();editing=false;editDraft=null;
  if(!saved&&deferredOrder&&!manual&&!confirmed)applyOrder(deferredOrder);
  deferredOrder=null;controls();
}
function saveEditor(event){
  event.preventDefault();const quantity=Number($('edit-quantity').value);let error='';
  if(!Number.isInteger(quantity)||quantity<1||quantity>20)error='Enter a quantity from 1 to 20.';
  editDraft.quantity=quantity;
  for(const category of ['drink','size','temperature','milk']){
    const value=$(`edit-${category}`).value.trim().toLowerCase();const valid=menu[category].find(choice=>choice.toLowerCase()===value);
    if(value&&!valid)error=`Choose a listed ${category} from the search results.`;
    if(category==='drink'&&!valid)error='Choose a drink from the menu.';
    editDraft[category==='drink'?'item':category]=valid||null;
  }
  if(error){$('editor-error').textContent=error;$('editor-error').hidden=false;return;}
  const before=editIndex<0?null:structuredClone(items[editIndex]);
  const index=editIndex<0?items.length:editIndex;items[index]=structuredClone(editDraft);edited.add(index);manual=true;$('reviewed').checked=false;
  corrections.push({at:new Date().toISOString(),action:before?'edit':'add',index,before,after:structuredClone(editDraft)});
  const wasDeferred=Boolean(deferredOrder);closeEditor(true);renderItems();note(wasDeferred?'Correction saved. Speech arrived during editing; check the live transcript.':'Correction saved. Listening can continue; your edits are protected.');
}
async function poll(){try{
  const status=await fetch('/api/status').then(r=>r.json());ready=status.ready;menu=status.menu;
  $('status').textContent=status.error|| (status.loading?'Loading local model…':ready?'Local model ready':'Model unavailable');$('status-dot').classList.toggle('amber',!ready);$('device').textContent=status.device?`${status.device.toUpperCase()} · offline`:'';
  for(const option of $('model').options)option.disabled=!status.models[option.value];if(ready){$('model').value=status.model;selectedModel=status.model;}controls();
}catch(error){ready=false;$('status').textContent='Local server disconnected';controls();}}
function cleanupAudio(){if(capture)capture.disconnect();if(source)source.disconnect();if(media)media.getTracks().forEach(track=>track.stop());if(context)context.close();capture=source=media=context=null;running=false;level=0;controls();}
async function start(){
  if(running)return;
  try{alertText('');media=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:false,noiseSuppression:true,autoGainControl:true},video:false});context=new AudioContext({sampleRate:16000});await context.resume();if(context.sampleRate!==16000)throw Error('Use current Chrome or Edge for 16 kHz audio.');
    await context.audioWorklet.addModule('/static/capture.js');source=context.createMediaStreamSource(media);capture=new AudioWorkletNode(context,'countercheck-capture');socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/audio`);socket.binaryType='arraybuffer';
    socket.onmessage=receive;socket.onclose=()=>{cleanupAudio();$('partial').textContent='';};socket.onerror=()=>alertText('Local audio connection failed.');
    await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.addEventListener('error',()=>reject(Error('Cannot open local audio connection.')),{once:true});});
    capture.port.onmessage=event=>{const frame=new Float32Array(event.data);level=Math.sqrt(frame.reduce((sum,v)=>sum+v*v,0)/frame.length);if(socket.readyState===WebSocket.OPEN){if(socket.bufferedAmount>192000){lost=true;alertText('Audio is falling behind. Start a new order and repeat the read-back.');controls();}else{socket.send(JSON.stringify({type:'audio_clock',captured_ms:performance.now()}));socket.send(event.data);}}};
    if(items.length&&!confirmed){manual=true;note('Existing draft kept after restarting capture. Use New order for a fresh automatic draft.');}
    source.connect(capture);capture.connect(context.destination);running=true;controls();
  }catch(error){socket?.close();cleanupAudio();alertText(error.message);}
}
function receive(message){
  const event=JSON.parse(message.data);
  if(event.type==='started'){currentEpoch=0;awaitingNewOrder=false;return;}
  if(event.type==='error'){alertText(event.text);return;}
  if(event.type==='audio_loss'){lost=true;alertText(event.text);controls();return;}
  if(event.type==='stopped'){socket?.close();return;}
  if(event.type==='new_order'){currentEpoch=event.epoch;awaitingNewOrder=false;lost=false;alertText('');return;}
  if(!['partial','final'].includes(event.type)||awaitingNewOrder||event.epoch!==currentEpoch)return;
  if(event.text&&event.metrics?.first_voice_client_ms!=null){const at=event.metrics.first_voice_client_ms;requestAnimationFrame(()=>{browserLatencies.push(performance.now()-at);browserLatencies=browserLatencies.slice(-2000);});}
  if(event.metrics){metrics.push(event.metrics);metrics=metrics.slice(-2000);$('response').textContent=`${Math.round(event.metrics.inference_response_ms)} ms`;$('rtf').textContent=event.metrics.rtf.toFixed(2);$('queue').textContent=String(event.metrics.queue);if(event.metrics.dropped_frames)lost=true;}
  if(event.type==='partial'){pending=Boolean(event.text);$('partial').textContent=event.text;if(!confirmed)$('reviewed').checked=false;}
  else{pending=false;$('partial').textContent='';if(event.text){raw.push(event.text);raw=raw.slice(-1000);renderTranscript();}if(event.order&&!confirmed){if(editing)deferredOrder=event.order;else applyOrder(event.order);}}
  if(event.order_overflow){lost=true;alertText('Order history limit reached. Start a new order; the microphone stays on.');}controls();
}
$('start').addEventListener('click',start);
$('stop').addEventListener('click',()=>{if(socket?.readyState===1){capture?.disconnect();source?.disconnect();media?.getTracks().forEach(track=>track.stop());socket.send(JSON.stringify({type:'stop'}));$('capture-state').textContent='Finishing last phrase…';$('stop').disabled=true;}});
$('new').addEventListener('click',()=>{if(editing)closeEditor(true);items=[];raw=[];warnings=[];decisions=[];corrections=[];edited.clear();manual=confirmed=pending=false;$('reviewed').checked=false;$('partial').textContent='';$('interpreted').textContent='';$('order-search').value='';note('');if(socket?.readyState===1){awaitingNewOrder=true;socket.send(JSON.stringify({type:'new_order'}));}else{lost=false;currentEpoch=0;}renderItems();renderNotes();renderTranscript();});
$('order-search').addEventListener('input',renderItems);$('clear-search').addEventListener('click',()=>{$('order-search').value='';renderItems();$('order-search').focus();});
$('add').addEventListener('click',()=>openEditor(-1));$('edit-form').addEventListener('submit',saveEditor);$('extras-search').addEventListener('input',renderExtras);
$('cancel-editor').addEventListener('click',()=>closeEditor());$('close-editor').addEventListener('click',()=>closeEditor());$('editor').addEventListener('cancel',event=>{event.preventDefault();closeEditor();});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('.menu-combo'))closeCombos();});
$('remove-item').addEventListener('click',()=>{const before=structuredClone(items[editIndex]);items.splice(editIndex,1);edited=new Set([...edited].filter(i=>i!==editIndex).map(i=>i>editIndex?i-1:i));manual=true;$('reviewed').checked=false;corrections.push({at:new Date().toISOString(),action:'remove',index:editIndex,before});closeEditor(true);renderItems();note('Item removed. Your manual corrections are protected.');});
$('unlock').addEventListener('click',()=>{confirmed=false;manual=true;$('reviewed').checked=false;renderItems();note('Order unlocked for correction. Review and confirm it again when finished.');});
$('reviewed').addEventListener('change',controls);
$('confirm').addEventListener('click',()=>{confirmed=true;receipts.push({confirmed_at:new Date().toISOString(),items:structuredClone(items),raw_transcript:[...raw],corrections:structuredClone(corrections)});receipts=receipts.slice(-200);note('');renderItems();});
$('export').addEventListener('click',()=>{const data={created_at:new Date().toISOString(),model:selectedModel,confirmed,items,raw_transcript:raw,warnings,menu_decisions:decisions,manual_corrections:corrections,metrics,browser_first_visible_text_proxy_ms:browserLatencies,confirmed_orders:receipts};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.download='countercheck-order.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);});
$('model').addEventListener('change',async()=>{ready=false;controls();try{const response=await fetch('/api/model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('model').value})});if(!response.ok)throw Error((await response.json()).detail);poll();}catch(error){alertText(error.message);poll();}});
document.querySelectorAll('.nav').forEach(button=>button.addEventListener('click',()=>{const tab=button.dataset.tab;$('desk').hidden=tab!=='desk';$('experiments').hidden=tab!=='experiments';document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b===button));$('page-title').textContent=tab==='desk'?'Order desk':'Model experiments';if(tab==='experiments')loadReport();}));
async function loadReport(){try{const report=await fetch('/api/report').then(r=>r.json());if(report.available===false){$('results').textContent=report.message;return;}
  $('results').innerHTML='<div class="table-scroll"><table><thead><tr><th>Model</th><th>Condition</th><th>WER ↓</th><th>CER ↓</th><th>RTF ↓</th><th>Audio/s ↑</th></tr></thead><tbody>'+(report.summaries||[]).map(s=>`<tr><td>${esc(s.model)}</td><td>${esc(s.condition)}</td><td>${(s.wer*100).toFixed(1)}%</td><td>${(s.cer*100).toFixed(1)}%</td><td>${s.rtf.toFixed(3)}</td><td>${s.throughput.toFixed(2)}</td></tr>`).join('')+'</tbody></table></div><p class="result-note">'+esc(report.interpretation||'')+'</p>';
  const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Inspect real transcription errors';details.append(summary);for(const row of (report.results||[]).filter(r=>r.word_errors>0).slice(0,20)){const p=document.createElement('p');p.textContent=`${row.model} / ${row.condition} — Reference: ${row.reference} | Heard: ${row.hypothesis}`;details.append(p);}$('results').append(details);
}catch(error){$('results').textContent='Cannot load the local evaluation report.';}}
function draw(){const canvas=$('wave'),ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);const n=22,t=performance.now()/150;for(let i=0;i<n;i++){const h=2+Math.min(30,level*400)*(.3+.7*Math.abs(Math.sin(i*.8+t)));ctx.fillStyle=running?'#33855f':'#c2ccd3';ctx.fillRect(i*canvas.width/n+2,(canvas.height-h)/2,3,h);}requestAnimationFrame(draw);}
poll();setInterval(poll,2000);draw();
window.counterCheckTest={getState:()=>({running,confirmed,items,raw,manual,lost,pending,editing,metrics,browserLatencies,corrections}),receive};
