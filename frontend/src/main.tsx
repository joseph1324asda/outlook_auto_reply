import React from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import 'antd/dist/reset.css';
import './style.css';

createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider locale={zhCN}><App /></ConfigProvider></React.StrictMode>);
