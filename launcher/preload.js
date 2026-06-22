const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('violetLauncher', {
  run: (command, extraArgs) => ipcRenderer.invoke('launcher:run', command, extraArgs),
  saveProfile: (form) => ipcRenderer.invoke('launcher:save-profile', form),
  selectStorageRoot: () => ipcRenderer.invoke('launcher:select-storage-root'),
  copyDiagnostics: () => ipcRenderer.invoke('launcher:copy-diagnostics'),
  appInfo: () => ipcRenderer.invoke('launcher:app-info')
});
