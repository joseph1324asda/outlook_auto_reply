import { App as AntApp, Layout, Menu } from 'antd';
import { useState } from 'react';
import Dashboard from './pages/Dashboard';
import Orders from './pages/Orders';
import Mapping from './pages/Mapping';
import Logs from './pages/Logs';
import Config from './pages/Config';
const pages: Record<string, JSX.Element> = { dashboard:<Dashboard/>, orders:<Orders/>, mapping:<Mapping/>, logs:<Logs/>, config:<Config/> };
export default function App() {
  const [page, setPage] = useState('dashboard');
  return <AntApp><Layout style={{ minHeight:'100vh' }}><Layout.Sider><div className="logo">SF → 飞书订单同步</div><Menu theme="dark" selectedKeys={[page]} onClick={(e)=>setPage(e.key)} items={[{key:'dashboard',label:'Dashboard'},{key:'orders',label:'订单列表'},{key:'mapping',label:'字段映射'},{key:'logs',label:'同步日志'},{key:'config',label:'系统配置'}]}/></Layout.Sider><Layout.Content className="content">{pages[page]}</Layout.Content></Layout></AntApp>;
}
