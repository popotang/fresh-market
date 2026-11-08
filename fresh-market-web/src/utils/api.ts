import { LOVRABET_RUNTIME_DOMAIN } from "@/api/public-domains";

// 简单封装 apiRequest
export const apiRequest = async (path: string, options: RequestInit = {}) => {
  const response = await fetch(`${LOVRABET_RUNTIME_DOMAIN}${path}`, {
    credentials: "include", // 关键配置：跨域请求携带Cookie
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  return response.json();
};
