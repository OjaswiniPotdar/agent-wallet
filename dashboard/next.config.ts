import type { NextConfig } from "next";

// Built as static files (dashboard/out) and served by the Python approvals server at /dashboard,
// so running the demo needs no Node.js. Node is only needed to rebuild after changing this folder.
const nextConfig: NextConfig = {
  output: "export",
  basePath: "/dashboard",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
