import React from 'react';
import {createRoot} from 'react-dom/client';
import Home from './entry-app';
import './globals.css';
import './color-theme.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><Home/></React.StrictMode>);
