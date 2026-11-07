"use client";
import {useCallback,useEffect,useState} from 'react';
import {api,post,User,date} from '@/lib/api';

type Stage={type:string;description:string;status:string;at:string};
type Doc={id:string|number;user_id:number;subject:string;body:string;category_slug:string;category_name:string;created_at:string;status:string;email_status:string;read:boolean;stages:Stage[]};
type SearchResult={items:Doc[];next_cursor:string|null;cache:'HIT'|'MISS'|string};
type Category={slug:string;name:string};
const states=['pending','processing','sent','failed'] as const;
const names:Record<string,string>={pending:'Ожидает',processing:'В обработке',sent:'Отправлено',failed:'Ошибка'};
const notes:Record<string,string>={pending:'Создано, но отправка не завершена',processing:'Выполняется обработка или отправка',sent:'SMTP принял письмо (не означает прочтение)',failed:'SMTP-отправка завершилась ошибкой'};

export default function FirestorePanel(){
 const [items,setItems]=useState<SearchResult>({items:[],next_cursor:null,cache:'—'});
 const [counts,setCounts]=useState<Record<string,number>>({});
 const [users,setUsers]=useState<User[]>([]),[categories,setCategories]=useState<Category[]>([]);
 const [filterStatus,setFilterStatus]=useState(''),[category,setCategory]=useState(''),[stage,setStage]=useState(''),[order,setOrder]=useState<'asc'|'desc'>('desc');
 const [cursor,setCursor]=useState<string|null>(null),[selected,setSelected]=useState<string|null>(null);
 const [stageType,setStageType]=useState('processing'),[stageText,setStageText]=useState('Этап добавлен администратором');
 const [demoUser,setDemoUser]=useState(0),[demoCategory,setDemoCategory]=useState('general');
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const load=useCallback(async(next:string|null=null)=>{
   try{
     setError('');const p=new URLSearchParams({limit:'15',order});
     if(filterStatus)p.set('status',filterStatus);if(category)p.set('category_slug',category);if(stage.trim())p.set('stage_type',stage.trim());if(next)p.set('cursor',next);
     const [result,stat]=await Promise.all([api<SearchResult>('/admin/notifications/search?'+p),api<Record<string,number>>('/admin/notifications/analytics')]);
     setItems(result);setCounts(stat);setCursor(next);
   }catch(e:any){setError(e.message)}
 },[filterStatus,category,stage,order]);
 useEffect(()=>{load()},[load]);
 useEffect(()=>{Promise.all([api<User[]>('/admin/users'),api<{categories:Category[]}>('/categories')]).then(([u,c])=>{const clients=u.filter(x=>x.role==='client');setUsers(clients);setDemoUser(clients[0]?.id||0);setCategories(c.categories)}).catch((e:Error)=>setError(e.message))},[]);
 async function run(work:()=>Promise<unknown>,success:string){setBusy(true);setError('');setMessage('');try{await work();setMessage(success);await load()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 async function changeState(doc:Doc,next:string){if(doc.status===next)return;await run(()=>api('/admin/notifications/'+doc.id,{method:'PATCH',body:JSON.stringify({status:next})}),'Состояние изменено. Этап и аналитические счётчики обновлены транзакционно.')}
 async function changeCategory(doc:Doc,next:string){if(doc.category_slug===next)return;await run(()=>api('/admin/notifications/'+doc.id,{method:'PATCH',body:JSON.stringify({category_slug:next})}),'Категория изменена вместе с данными связанной коллекции.')}
 async function addStage(){if(!selected)return;await run(()=>post('/admin/notifications/'+selected+'/stages',{type:stageType,description:stageText,status:'completed'}),'Этап сохранён внутри документа Firestore.');setSelected(null)}
 const current=items.items.find(x=>String(x.id)===selected);
 return <><div className="page-heading"><div><span className="eyebrow">АДМИНИСТРИРОВАНИЕ · ЛАБОРАТОРНАЯ №2</span><h1>Firestore Lab</h1><p>Эксперименты над уведомлениями: статусы, транзакции, этапы, индексы, Redis-кэш.</p></div></div>
 <div className="panel pad" style={{marginBottom:20}}><h2>Что означают состояния</h2><div className="status-grid">{states.map(s=><div key={s} className="status-help"><strong>{names[s]}</strong><small><code>{s}</code> · {notes[s]}</small></div>)}</div><p className="hint">Здесь смена состояния — учебная операция: она не отправляет письмо. В центре уведомлений письмо можно повторно отправить только после ошибки. Прочтение клиентом (`read`) — отдельное событие.</p></div>
 <div className="panel pad" style={{marginBottom:20}}><h2>Аналитика · количество по состояниям</h2><div className="status-grid">{states.map(s=><div key={s} className="metric-box"><span>{names[s]}</span><b>{counts[s]??0}</b></div>)}</div><p className="hint">При изменении статуса обновляются исходный документ, вложенный этап `status_change` и счётчики `notification_stats` в одной транзакции Firestore.</p></div>
 <div className="two-cols" style={{marginTop:0,marginBottom:20}}>
 <div className="panel pad"><h2>Создать тестовое уведомление</h2><p className="hint">Не отправляет email. Создаёт документ в состоянии «Ожидает», чтобы можно было проверить переходы и аналитику.</p><div className="stack"><label>Получатель<select value={demoUser} onChange={e=>setDemoUser(Number(e.target.value))}>{users.map(u=><option key={u.id} value={u.id}>{u.full_name} — {u.email}</option>)}</select></label><label>Категория<select value={demoCategory} onChange={e=>setDemoCategory(e.target.value)}>{categories.map(c=><option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label><button disabled={busy||!demoUser} className="button" onClick={()=>run(()=>post('/admin/lab/notifications/demo',{user_id:demoUser,category_slug:demoCategory}),'Тестовое уведомление создано в Firestore (без email).')}>Создать тестовый документ</button></div></div>
 <div className="panel pad"><h2>Индексируемый поиск</h2><div className="stack"><div className="form-row"><label>Состояние<select value={filterStatus} onChange={e=>setFilterStatus(e.target.value)}><option value="">Все</option>{states.map(s=><option value={s} key={s}>{names[s]}</option>)}</select></label><label>Категория<select value={category} onChange={e=>setCategory(e.target.value)}><option value="">Все</option>{categories.map(c=><option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label></div><div className="form-row"><label>Вложенный этап<input value={stage} onChange={e=>setStage(e.target.value)} placeholder="created, status_change..."/></label><label>Сортировка<select value={order} onChange={e=>setOrder(e.target.value === 'asc' ? 'asc' : 'desc')}><option value="desc">Сначала новые</option><option value="asc">Сначала старые</option></select></label></div><button disabled={busy} className="button outline" onClick={()=>load()}>Обновить результаты</button><small>Ответ: Redis {items.cache}, TTL — 45 сек. Изменения инвалидируют кэш.</small></div></div>
 </div>
 <div className="panel pad"><div className="section-title" style={{marginTop:0}}><div><h2>Документы Firestore</h2><p>Для тестирования выбери состояние или категорию. Нажми «История», чтобы увидеть этапы.</p></div></div>
 {items.items.length===0&&<p className="muted">Уведомления по заданным фильтрам не найдены.</p>}
 {items.items.map(n=><div className="manage-row notification-admin-row" key={n.id}><div className="grow"><b>{n.subject}</b><small>#{n.id} · {n.category_name||n.category_slug} · {date(n.created_at)}</small><p>{n.body}</p><span className="tag">{names[n.status]||n.status}</span><span className="tag" style={{marginLeft:6}}>Email: {n.email_status}</span></div><div className="notice-controls"><label>Состояние<select disabled={busy} value={n.status} onChange={e=>changeState(n,e.target.value)}>{states.map(s=><option key={s} value={s}>{names[s]}</option>)}</select></label><label>Категория<select disabled={busy} value={n.category_slug} onChange={e=>changeCategory(n,e.target.value)}>{categories.map(c=><option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label><button className="button outline small" onClick={()=>setSelected(String(n.id))}>История и этапы ({n.stages?.length??0})</button></div></div>)}
 <div className="actions"><button className="button outline small" disabled={busy||!items.next_cursor} onClick={()=>load(items.next_cursor)}>Следующая страница</button>{cursor&&<button className="button outline small" onClick={()=>load()}>Первая страница</button>}</div></div>
 {current&&<div className="panel pad" style={{marginTop:20}}><div className="section-title" style={{marginTop:0}}><div><h2>История обработки</h2><p>{current.subject}</p></div><button className="button outline small" onClick={()=>setSelected(null)}>Закрыть</button></div><div className="timeline">{(current.stages||[]).map((s,i)=><div key={i} className="timeline-entry"><strong>{s.type} · {s.status}</strong><small>{date(s.at)}</small><p>{s.description||'—'}</p></div>)}</div><hr/><div className="stack"><h3>Добавить этап обработки</h3><div className="form-row"><label>Тип этапа<input value={stageType} onChange={e=>setStageType(e.target.value)}/></label><label>Описание<input value={stageText} onChange={e=>setStageText(e.target.value)}/></label></div><button className="button" disabled={busy||!stageType.trim()} onClick={addStage}>Добавить этап транзакционно</button></div></div>}
 {error&&<div className="alert error">{error}</div>}{message&&<div className="alert success">{message}</div>}
 </>;
}
