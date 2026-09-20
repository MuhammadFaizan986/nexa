import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The browser talks to the FastAPI backend directly (CORS is enabled there),
  // so this app is a pure client: no server-side secrets live here.
  reactStrictMode: true,
};

export default nextConfig;
