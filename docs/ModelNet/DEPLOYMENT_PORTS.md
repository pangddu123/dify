# Dify 本地 Docker 部署 — 端口占用清单

本文档记录本仓库在 `/home/xianghe/temp/dify/docker` 下使用 `docker compose` 拉起的
Dify 全家桶所暴露到宿主机的端口。为避免与机器上已经存在的容器/服务冲突，所有对
外端口从 **9100** 起顺序探测后选用，`9103` 探测时已被占用故跳过。

部署时间：2026-04-24
Compose 项目名：`dify`（通过 `.env` 中 `COMPOSE_PROJECT_NAME=dify` 固定，避免
落回默认的 `docker` —— 宿主机已有同名 compose 项目在运行）
镜像版本：
- `dify-modelnet-api:local` — 本地从 `api/Dockerfile` 构建，包含 ModelNet
  P1.5+ 的 response-aggregator 节点后端
- `dify-modelnet-web:local` — 本地从 `web/Dockerfile` 构建，包含前端 9 点
  注册
- `langgenius/dify-plugin-daemon:0.5.3-local`（上游）
- `postgres:15-alpine` / `redis:6-alpine` / `nginx:latest` /
  `semitechnologies/weaviate:1.27.0` / `langgenius/dify-sandbox:0.2.14` /
  `ubuntu/squid:latest`（均为上游）

向量库：Weaviate（默认 profile，未暴露端口）

---

## 一、对外暴露的宿主机端口

| 宿主端口 | 容器端口 | 容器                   | 变量（`docker/.env`）         | 用途                                                 |
|---------:|---------:|------------------------|-------------------------------|------------------------------------------------------|
| **9100** | 80       | `dify-nginx-1`         | `EXPOSE_NGINX_PORT`           | Dify 主入口（Console、Web App、API 都走这一端口） |
| **9101** | 443      | `dify-nginx-1`         | `EXPOSE_NGINX_SSL_PORT`       | HTTPS 入口（目前未配置证书，仅占位）                 |
| **9102** | 5003     | `dify-plugin_daemon-1` | `EXPOSE_PLUGIN_DEBUGGING_PORT`| 插件远程调试端口（本地开发插件时才用）               |

### 访问地址

- **Web / Console**：<http://127.0.0.1:9100>
- **初次安装**：<http://127.0.0.1:9100/install>
- **Console API 自检**：<http://127.0.0.1:9100/console/api/setup>

> 注意：如果你的 shell 环境里设置了 `HTTP_PROXY` / `HTTPS_PROXY`（例如通过公司
> Squid 上外网），直接 `curl http://127.0.0.1:9100/` 会被代理吃掉返回 503。
> 用 `curl --noproxy '*' http://127.0.0.1:9100/` 或浏览器直连即可。

---

## 二、所有容器（含仅内部通信的）

| 容器名                    | 镜像                                      | 端口映射                 |
|---------------------------|-------------------------------------------|--------------------------|
| `dify-nginx-1`            | `nginx:latest`                            | 9100→80, 9101→443        |
| `dify-api-1`              | `langgenius/dify-api:1.13.3`              | 5001/tcp（内部）         |
| `dify-worker-1`           | `langgenius/dify-api:1.13.3`              | 5001/tcp（内部）         |
| `dify-worker_beat-1`      | `langgenius/dify-api:1.13.3`              | 5001/tcp（内部）         |
| `dify-web-1`              | `langgenius/dify-web:1.13.3`              | 3000/tcp（内部）         |
| `dify-plugin_daemon-1`    | `langgenius/dify-plugin-daemon:0.5.3-local` | 9102→5003               |
| `dify-db_postgres-1`      | `postgres:15-alpine`                      | 5432/tcp（内部）         |
| `dify-redis-1`            | `redis:6-alpine`                          | 6379/tcp（内部）         |
| `dify-weaviate-1`         | `semitechnologies/weaviate:1.27.0`        | 8080/tcp（内部）         |
| `dify-sandbox-1`          | `langgenius/dify-sandbox:0.2.14`          | 8194/tcp（内部）         |
| `dify-ssrf_proxy-1`       | `ubuntu/squid:latest`                     | 3128/tcp（内部）         |

内部端口只在 `dify_default` / `dify_ssrf_proxy_network` 两个 compose 网络里可见，
不占用宿主机端口。

---

## 三、端口选择依据

选用前在宿主机上执行了以下排查，保证与现有服务不撞：

```bash
# 1) 所有宿主监听端口（节选 9000-9300 区间）
ss -tln | awk 'NR>1 {print $4}' | grep -oE '[0-9]+$' | sort -un \
  | awk '$1 >= 9000 && $1 <= 9300'
# → 9000, 9090, 9091, 9095, 9099, 9103, 9200

# 2) 所有 docker 容器已映射端口（同区间）
docker ps -a --format "{{.Ports}}" | grep -oE '[0-9]+->' | grep -oE '^[0-9]+' \
  | sort -un | awk '$1 >= 9000 && $1 <= 9300'
# → 9000, 9001, 9009, 9090, 9091, 9095, 9099, 9200, 9300

# 3) 逐个试连
for p in 9100 9101 9102 9103 9104; do
  timeout 1 bash -c "</dev/tcp/127.0.0.1/$p" 2>/dev/null \
    && echo "$p: IN USE" || echo "$p: free"
done
# → 9100 free / 9101 free / 9102 free / 9103 IN USE / 9104 free
```

于是按顺序取 `9100, 9101, 9102`（跳过 `9103`）。

### 不改动现有容器的保证

- `COMPOSE_PROJECT_NAME=dify` 把所有新容器/网络命名空间压到 `dify-*` / `dify_*`，
  与已存在的 `docker_*`、`open-webui_*`、`langfuse_*`、`cos*`、`new-api_*` 等互不
  影响。
- 未声明 `container_name`（docker-compose.yaml 里 Dify 11 个核心服务都没写死名字），
  因此不会抢占宿主上已有的裸名字容器（如 `postgres`、`redis`、`open-webui`、
  `new-api`、`litellm-litellm-1` 等）。
- 仅使用默认的 Weaviate 向量库 profile；`milvus-etcd` / `iris` / `oceanbase` /
  `opensearch` / `couchbase-server` / `myscale` / `milvus-standalone` 等**确实**
  写死了 container_name 的 profile 全部未启用。

部署前、后对 `docker ps` 的对比显示，`postgres`、`redis`、`open-webui`、
`open-webui-tob`、`new-api`、`litellm-litellm-1` 等既有容器状态保持不变。

---

## 四、常用运维命令

```bash
# 进入 compose 所在目录（所有命令都要在这里执行）
cd /home/xianghe/temp/dify/docker

# 查看 Dify 相关容器状态
docker compose ps

# 查看某个服务的日志
docker compose logs -f api
docker compose logs -f nginx

# 停止但保留数据卷
docker compose stop

# 重新启动
docker compose start

# 彻底销毁（会删除容器，但 ./volumes/ 下的数据仍在）
docker compose down

# 若要连数据卷一起删（谨慎）：
# docker compose down -v
```

---

## 五、自定义镜像构建（本 fork 专用）

上游 `langgenius/dify-api:1.13.3` / `dify-web:1.13.3` 不包含本 fork 新增的
`response-aggregator` 节点 —— 直接用上游镜像导入 `docs/ModelNet/examples/`
里的 DSL 会在画布上触发 React #130（前端找不到节点组件）。

`docker/docker-compose.override.yaml` 把 4 个 Dify 自身的服务重定向到本地
构建的镜像：`api` / `worker` / `worker_beat` 共用 `dify-modelnet-api:local`，
`web` 用 `dify-modelnet-web:local`。

### 已知陷阱：buildkit 读上下文会撞 root-owned 的 `docker/volumes/`

`web` 的构建上下文是仓库根，但 `docker/volumes/db/data/pgdata` 是
postgres 容器以 root 写入的，宿主用户读不了；buildkit 不会因为
`.dockerignore` 提前跳过已经拒绝打开的目录，所以 `docker compose build web`
会直接报 `permission denied`。

解决办法：把一个筛过的构建上下文同步到 `/tmp`，然后从那里直接
`docker build`。仓库根和 `web/Dockerfile` 本身没动：

```bash
# 一次性准备干净的构建上下文
rsync -a --delete \
  --exclude='docker/volumes' --exclude='.git' \
  --exclude='node_modules' --exclude='**/node_modules' \
  --exclude='api/.venv' --exclude='api/storage' --exclude='api/logs' \
  --exclude='web/.next' --exclude='web/dist' \
  --exclude='**/__pycache__' --exclude='**/.pytest_cache' \
  /home/xianghe/temp/dify/ /tmp/dify-build-ctx/

# web（上下文 = 仓库根）
cd /tmp/dify-build-ctx
docker build -f web/Dockerfile -t dify-modelnet-web:local .

# api（上下文 = api 子目录）
cd /tmp/dify-build-ctx/api
docker build -t dify-modelnet-api:local .

# 热替换容器
cd /home/xianghe/temp/dify/docker
docker compose up -d
```

`docker-compose.override.yaml` 里的 `build:` 块仅供未来 buildkit 修复后
使用，目前实际生效的是我们 `docker build` 出来的 tag。

---

## 六、修改端口的方法

端口全部在 `docker/.env` 里，改完 `docker compose up -d` 即可生效：

```
EXPOSE_NGINX_PORT=9100        # HTTP 主入口
EXPOSE_NGINX_SSL_PORT=9101    # HTTPS
EXPOSE_PLUGIN_DEBUGGING_PORT=9102  # 插件远程调试
```

不要修改 `docker-compose.yaml` —— 该文件已在头部注释明确要求"Do not modify this
file directly. Instead, update the .env"。
