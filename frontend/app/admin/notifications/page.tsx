"use client";
import {useEffect,useState} from 'react';
import Link from 'next/link';
import {api,post,Notice,date} from '@/lib/api';
type Recipient={id:number;email:string;full_name:string;role:string};
type Template={id:number;name:string;subject:string;body:string;category_slug:string};
type ExtendedNotice=Notice&{status:string;stages?:{type:string;description:string;status:string;at:string}[]};
const labels:Record<string,string>={pending:'Ожидает',processing:'В обработке',sent:'Отправлено',failed:'Ошибка'};
export default function AdminNotifications(){
 const [users,setUsers]=useState<Recipient[]>([]),[cats,setCats]=useState<{slug:string;name:string}[]>([]),[templates,setTemplates]=useState<Template[]>([]),[history,setHistory]=useState<ExtendedNotice[]>([]);
 const [f,setF]=useState({everyone:false,user_id:0,category_slug:'general',subject:'',body:''});
 const [t,setT]=useState({name:'',subject:'',body:'',category_slug:'general'});
 const [expanded,setExpanded]=useState<string|null>(null),[busy,setBusy]=useState<string|null>(null);
 const [error,setError]=useState(''),[message,setMessage]=useState('');
 const [categoriesError,setCategoriesError]=useState(''),[categoriesLoading,setCategoriesLoading]=useState(true);
 async function loadCategories(){
  setCategoriesLoading(true);
  setCategoriesError('');
  try {
   const response=await api<{categories:{slug:string;name:string}[]}>('/categories');
   if(!Array.isArray(response.categories)) throw new Error('Некорректный ответ API категорий');
   setCats(response.categories);
   setF(current=>({...current,category_slug:response.categories.some(c=>c.slug===current.category_slug)?current.category_slug:response.categories[0]?.slug||''}));
   setT(current=>({...current,category_slug:response.categories.some(c=>c.slug===current.category_slug)?current.category_slug:response.categories[0]?.slug||''}));
   if(response.categories.length===0) setCategoriesError('В Firestore нет активных категорий уведомлений.');
  } catch(e:unknown){
   setCats([]);
   setCategoriesError(e instanceof Error?e.message:'Не удалось получить категории');
  } finally {setCategoriesLoading(false)}
 }
 async function load(){
  // Categories are loaded independently: a failure in history, templates or users
  // must never make the category selector empty.
  const tasks=await Promise.allSettled([
   loadCategories(),
   api<Recipient[]>('/admin/users'),
   api<Template[]>('/admin/templates'),
   api<ExtendedNotice[]>('/admin/notifications')
  ]);
  if(tasks[1].status==='fulfilled'){
   const usersList=tasks[1].value;
   setUsers(usersList.filter(x=>x.role==='client'));
   setF(x=>({...x,user_id:x.user_id||usersList.find(v=>v.role==='client')?.id||0}));
  }
  if(tasks[2].status==='fulfilled')setTemplates(tasks[2].value);
  if(tasks[3].status==='fulfilled')setHistory(tasks[3].value);
  const failures=tasks.slice(1).filter((result):result is PromiseRejectedResult=>result.status==='rejected');
  if(failures.length)setError(failures.map(x=>x.reason instanceof Error?x.reason.message:String(x.reason)).join('; '));
  else setError('');
 }
 useEffect(()=>{load()},[]);
 async function send(e:React.FormEvent){e.preventDefault();setError('');if(!cats.some(c=>c.slug===f.category_slug)){setError('Выберите доступную категорию уведомления');return}try{const result=await post<{sent_to:number}>('/admin/notifications/send',{...f,user_id:f.everyone?null:f.user_id});setMessage('Обработано уведомлений: '+result.sent_to);await load()}catch(e:any){setError(e.message)}}
 async function saveTemplate(e:React.FormEvent){e.preventDefault();setError('');if(!cats.some(c=>c.slug===t.category_slug)){setError('Выберите доступную категорию для шаблона');return}try{await post('/admin/templates',t);setMessage('Шаблон сохранён');await load()}catch(e:any){setError(e.message)}}
 async function retry(note:ExtendedNotice){if(!confirm('Повторить SMTP-отправку уведомления «'+note.subject+'»? Клиент может получить ещё одно письмо.'))return;setBusy(String(note.id));setError('');setMessage('');try{const updated=await post<ExtendedNotice>('/admin/notifications/'+note.id+'/retry',{});setMessage(updated.status==='sent'?'Повторная отправка выполнена.':'Повторная попытка не удалась. Проверьте Mailpit и SMTP.');await load()}catch(e:any){setError(e.message)}finally{setBusy(null)}}
 return <><div className="page-heading"><div><span className="eyebrow">КОММУНИКАЦИИ</span><h1>Центр уведомлений</h1><p>Рассылки, шаблоны, этапы обработки и повторная отправка после ошибки.</p></div><Link className="button outline small" href="/admin/firestore">Открыть Firestore Lab</Link></div>
 <div className="two-cols"><div className="panel pad"><h2>Отправить уведомление</h2><form onSubmit={send} className="stack"><label className="check"><input type="checkbox" checked={f.everyone} onChange={e=>setF({...f,everyone:e.target.checked})}/> Отправить всем клиентам</label>{!f.everyone&&<label>Получатель<select value={f.user_id} onChange={e=>setF({...f,user_id:Number(e.target.value)})}>{users.map(u=><option value={u.id} key={u.id}>{u.full_name} — {u.email}</option>)}</select></label>}<label>Применить шаблон<select value="" onChange={e=>{const template=templates.find(x=>x.id===Number(e.target.value));if(template)setF({...f,subject:template.subject,body:template.body,category_slug:template.category_slug})}}><option value="">Не выбран</option>{templates.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Категория<select value={f.category_slug} disabled={categoriesLoading||cats.length===0} onChange={e=>setF({...f,category_slug:e.target.value})}>{cats.length===0&&<option value="">{categoriesLoading?'Загрузка категорий…':'Категории недоступны'}</option>}{cats.map(c=><option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label>{categoriesError&&<div className="alert error">Категории: {categoriesError} <button type="button" className="button outline small" onClick={loadCategories}>Повторить загрузку</button></div>}<label>Тема<input required value={f.subject} onChange={e=>setF({...f,subject:e.target.value})}/></label><label>Текст<textarea required rows={4} value={f.body} onChange={e=>setF({...f,body:e.target.value})}/></label><button className="button" disabled={categoriesLoading||!cats.some(c=>c.slug===f.category_slug)||(!f.everyone&&!f.user_id)}>Отправить уведомление</button></form><hr/><h2>Новый шаблон</h2><form className="stack" onSubmit={saveTemplate}><label>Название<input required value={t.name} onChange={e=>setT({...t,name:e.target.value})}/></label><label>Тема<input required value={t.subject} onChange={e=>setT({...t,subject:e.target.value})}/></label><label>Текст<textarea required value={t.body} onChange={e=>setT({...t,body:e.target.value})}/></label><label>Категория<select value={t.category_slug} disabled={categoriesLoading||cats.length===0} onChange={e=>setT({...t,category_slug:e.target.value})}>{cats.length===0&&<option value="">Нет доступных категорий</option>}{cats.map(c=><option value={c.slug} key={c.slug}>{c.name}</option>)}</select></label><button className="button outline" disabled={categoriesLoading||!cats.some(c=>c.slug===t.category_slug)}>Сохранить шаблон</button></form></div>
 <div className="panel pad"><div className="section-title" style={{marginTop:0}}><div><h2>История отправок</h2><p>Основной статус обработки и результат SMTP — разные поля.</p></div><button className="button outline small" onClick={load}>Обновить</button></div>{history.length===0&&<p className="muted">Уведомлений пока нет</p>}{history.map(n=><div key={n.id} className="manage-row" style={{display:'block'}}><div style={{display:'flex',gap:12,alignItems:'start'}}><div className="grow"><b>{n.subject}</b><small>Получатель ID {n.user_id} · {date(n.created_at)}</small><p>{n.body}</p><span className="tag">{labels[n.status]||n.status||'—'}</span><span className="tag" style={{marginLeft:6}}>Email: {n.email_status}</span></div></div><div className="actions"><button className="button outline small" onClick={()=>setExpanded(expanded===String(n.id)?null:String(n.id))}>{expanded===String(n.id)?'Скрыть историю':'История обработки'}</button>{n.status==='failed'&&<button className="button small" disabled={busy===String(n.id)} onClick={()=>retry(n)}>{busy===String(n.id)?'Отправка…':'Повторить email'}</button>}</div>{expanded===String(n.id)&&<div className="timeline">{n.stages?.map((s,i)=><div key={i} className="timeline-entry"><strong>{s.type} · {s.status}</strong><small>{date(s.at)}</small><p>{s.description||'—'}</p></div>)}</div>}</div>)}</div></div>
 {error&&<div className="alert error">{error}</div>}{message&&<div className="alert success">{message}</div>}
 </>;
}
