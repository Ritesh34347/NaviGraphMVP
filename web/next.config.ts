import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // "standalone" makes `next build` emit a self-contained server in
  // .next/standalone that only includes the files actually needed at
  // runtime (plus a pruned node_modules). The Dockerfile copies just that
  // output instead of the full project + node_modules, which keeps the
  // production image small and the container startup fast.
  output: "standalone",
};

export default nextConfig;
