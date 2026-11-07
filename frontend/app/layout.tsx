import type {Metadata} from 'next';
import Shell from '@/components/Shell';
import './globals.css';
export const metadata:Metadata={title:'CinemaSpace — кинотеатр',description:'Умный личный кабинет кинотеатра'};
export default function RootLayout({children}:{children:React.ReactNode}) {return <html lang="ru"><body><Shell>{children}</Shell></body></html>}
