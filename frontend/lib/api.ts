export const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
export type User = {id:number;email:string;full_name:string;role:'client'|'admin'};
export type Movie = {id:number;title:string;description:string;genre:string;duration_min:number;rating:string;poster_url:string;active:boolean};
export type Screening = {id:number;start_at:string;price_cents:number;movie:Movie;hall:{id:number;name:string;rows:number;seats_per_row:number}};
export type Notice = {id:string|number;subject:string;body:string;category_slug:string;created_at:string;read:boolean;email_status:string;user_id:number};
export function token() { if (typeof window === 'undefined') return ''; return localStorage.getItem('cinema_token') || ''; }
export function getUser():User|null {if(typeof window==='undefined') return null; try {return JSON.parse(localStorage.getItem('cinema_user')||'null')}catch{return null}}
export function saveAuth(x:{token:string;user:User}) {localStorage.setItem('cinema_token',x.token);localStorage.setItem('cinema_user',JSON.stringify(x.user));}
export function logout(){localStorage.removeItem('cinema_token');localStorage.removeItem('cinema_user');location.href='/login';}
export async function api<T=any>(path:string, options:RequestInit={}):Promise<T> {
 const headers:Record<string,string>={...((options.headers as Record<string,string>)||{})};
 if(options.body && typeof options.body === 'string') headers['Content-Type']='application/json';
 if(token()) headers.Authorization='Bearer '+token();
 const response=await fetch(API+path,{...options,headers,cache:'no-store'});
 const data=await response.json().catch(()=>({detail:'Ошибка связи с сервером'}));
 if(!response.ok) {
  const details = Array.isArray(data.detail) ? data.detail.map((item: {msg?: string}) => item.msg || 'Некорректные данные').join('; ') : null;
  throw new Error(typeof data.detail==='string' ? data.detail : details || `Ошибка HTTP ${response.status}`);
 }
 return data as T;
}
export const post=<T=any>(url:string,body:unknown)=>api<T>(url,{method:'POST',body:JSON.stringify(body)});
export function money(cents:number){return (cents/100).toLocaleString('ru-RU',{maximumFractionDigits:0})+' ₽'}
export function date(x:string){return new Date(x).toLocaleString('ru-RU',{dateStyle:'medium',timeStyle:'short'})}
