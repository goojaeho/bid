const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('jarvis',{
  wake:value=>ipcRenderer.invoke('wake',value),
  show:()=>ipcRenderer.invoke('show'),
  transcribe:(bytes,mode)=>ipcRenderer.invoke('transcribe',bytes,mode),
  onStopRecording:callback=>ipcRenderer.on('stop-recording',()=>callback()),
  state:()=>ipcRenderer.invoke('state'),login:()=>ipcRenderer.invoke('login'),logout:()=>ipcRenderer.invoke('logout'),
  site:()=>ipcRenderer.invoke('site'),
  pause:value=>ipcRenderer.invoke('pause',value),settings:value=>ipcRenderer.invoke('settings',value),
  command:text=>ipcRenderer.invoke('command',text),tts:text=>ipcRenderer.invoke('tts',text),
  onState:callback=>ipcRenderer.on('state',(_e,value)=>callback(value)),
  onNotice:callback=>ipcRenderer.on('notice',(_e,value)=>callback(value))
});
