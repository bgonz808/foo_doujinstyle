import { createSecureServer } from "node:http2";
import { readFileSync } from "node:fs";

const [key, cert, delay, close] = process.argv.slice(2);
const pause = Number(delay);
if (!key || !cert || !Number.isFinite(pause) || pause < 0) throw new Error("Pass TLS key, certificate and delay in ms");
const bytes = Buffer.alloc(8 * 1024 * 1024, 65);
const server = createSecureServer({ key: readFileSync(key), cert: readFileSync(cert), allowHTTP1: true });
server.on("request", (request, response) => {
    const range = /^bytes=(\d+)-(\d+)$/.exec(request.headers.range ?? "");
    const first = Number(range?.[1]);
    const last = Number(range?.[2]);
    if (!range || first > last || last >= bytes.length) {
        response.writeHead(416, { "content-length": 0 });
        response.end();
        return;
    }
    const chunk = bytes.subarray(first, last + 1);
    const headers = {
        "content-length": chunk.length,
        "content-range": `bytes ${first}-${last}/${bytes.length}`,
        etag: '"transport-fixture-v1"',
    };
    if (close === "close" && request.httpVersionMajor === 1) headers.connection = "close";
    setTimeout(() => {
        response.writeHead(206, headers);
        response.end(chunk);
    }, pause);
});
server.listen(0, "127.0.0.1", () => process.stdout.write(`${server.address().port}\n`));
