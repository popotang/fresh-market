import React from "react";
import { createRoot } from "react-dom/client";
import { isInIcestark } from "@ice/stark-app";
import { ConfigProvider } from "antd";
import enUS from "antd/locale/en_US";
import { YtConfigProvider } from "@lovrabet/components";
import { apiFetch, runtimeFetch } from "./api/fetch";
import App from "./router";
import "./style.css";

// 应用根：在此注入一次请求实例，其下所有 Lovrabet 组件共享，
// 无需逐个组件传递 apiFetch / runtimeFetch
function AppRoot(props: object = {}) {
  return (
    <ConfigProvider locale={enUS}>
      <YtConfigProvider apiFetch={apiFetch} runtimeFetch={runtimeFetch}>
        <App {...props} />
      </YtConfigProvider>
    </ConfigProvider>
  );
}

// 可选：根据 isInIcestark() 判断当前的运行环境，可同时兼容独立使用和嵌入使用
if (!isInIcestark()) {
  const container = document.getElementById("root");
  if (container) {
    const root = createRoot(container);
    root.render(<AppRoot />);
  }
}

// 关键：暴露 mount 供主应用加载时调用
export function mount({
  container,
  customProps,
}: {
  container: HTMLElement;
  customProps: object;
}) {
  let root = (container as any)._reactRoot;
  if (!root) {
    root = createRoot(container);
    (container as any)._reactRoot = root;
  }

  root.render(<AppRoot {...customProps} />);
  return root;
}

// 关键：暴露 unmount 供主应用卸载时调用
export function unmount({ container }: { container: HTMLElement }) {
  // React 18 中不再需要手动卸载，但为了兼容性保留
  const root = (container as any)._reactRoot;
  if (root) {
    root.unmount();
    delete (container as any)._reactRoot;
  }
}
