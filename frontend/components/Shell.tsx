"use client";
import Link from 'next/link';
import {usePathname} from 'next/navigation';
import {useEffect,useState} from 'react';
import {Film,Ticket, Bell, CalendarDays, LayoutDashboard, Send, FlaskConical, LogOut, Menu, X, Sparkles} from 'lucide-react';
import {getUser,logout,User} from '@/lib/api';

export default function Shell({children}:{children:React.ReactNode}){
 const [user,setUser]=useState<User|null>(null);const [menu,setMenu]=useState(false);const path=usePathname();
 useEffect(()=>setUser(getUser()),[path]);
 const links=[{href:'/',label:'Обзор',icon:LayoutDashboard},{href:'/movies',label:'Афиша и сеансы',icon:Film},{href:'/bookings',label:'Мои билеты',icon:Ticket},{href:'/notifications',label:'Уведомления',icon:Bell}];
 const admin=[{href:'/admin',label:'Обзор системы',icon:LayoutDashboard},{href:'/admin/movies',label:'Фильмы',icon:Film},{href:'/admin/sessions',label:'Сеансы и залы',icon:CalendarDays},{href:'/admin/notifications',label:'Рассылки',icon:Send},{href:'/admin/lab',label:'Redis Lab',icon:FlaskConical},{href:'/admin/firestore',label:'Firestore Lab',icon:FlaskConical}];
 const auth=path==='/login'||path==='/register';
 if(auth) return <main className="auth-layout">{children}</main>;
 return <div className="layout">
  <aside className={'sidebar '+(menu?'open':'')}>
   <Link className="brand" href="/"><span className="brand-icon"><Film size={20}/></span><span>CINEMA<span className="brand-light">SPACE</span><small>ваш кинотеатр</small></span></Link>
   <div className="nav-group-label">ЛИЧНЫЙ КАБИНЕТ</div>
   <nav>{links.map(({href,label,icon:Icon})=><Link onClick={()=>setMenu(false)} key={href} className={'nav-link '+(path===href?'active':'')} href={href}><Icon size={18}/>{label}</Link>)}</nav>
   {user?.role==='admin'&&<><div className="nav-group-label">УПРАВЛЕНИЕ</div><nav>{admin.map(({href,label,icon:Icon})=><Link onClick={()=>setMenu(false)} key={href} className={'nav-link '+(path===href?'active':'')} href={href}><Icon size={18}/>{label}</Link>)}</nav></>}
   <div className="sidebar-bottom"><div className="user-avatar">{(user?.full_name||'Г').slice(0,1).toUpperCase()}</div><div className="user-info"><b>{user?.full_name||'Гость'}</b><small>{user?.email||'Войдите в аккаунт'}</small></div>{user&&<button className="icon-button" title="Выйти" onClick={logout}><LogOut size={17}/></button>}</div>
  </aside>
  <div className="main-area"><header className="topbar"><button className="icon-button menu-btn" onClick={()=>setMenu(!menu)}>{menu?<X/>:<Menu/>}</button><div className="top-label"><Sparkles size={15}/> Кино начинается здесь</div><div className="top-right">{user?<span className="pill">{user.role==='admin'?'Администратор':'Клиент'}</span>:<Link className="button small" href="/login">Войти</Link>}</div></header><main className="content">{children}</main></div>
 </div>;
}
