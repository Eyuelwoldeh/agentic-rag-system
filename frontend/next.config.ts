import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // self-contained server for the Docker image (frontend/Dockerfile)
};

export default nextConfig;
