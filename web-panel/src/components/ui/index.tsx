export * from './foundation';
export * from './forms';
export * from './data';
export * from './overlays';
import React from 'react';
import { message, notification, Modal } from './overlays';
const UiApp=({children}:{children?:React.ReactNode})=><div className="ui-app">{children}</div>;
export const App=Object.assign(UiApp,{useApp:()=>({message,notification,modal:Modal})});
