import React from 'react';
import {createRoot} from 'react-dom/client';
import Home from './entry-app';
import {AuthProvider,AuthNotice} from './auth';
import './globals.css';
import './color-theme.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><AuthProvider><AuthNotice/><Home/></AuthProvider></React.StrictMode>);
