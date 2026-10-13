"""組裝：同一個 image，依 ROLE 決定這個 process 扮演什麼。

ROLE=all      單 process dev 模式：GatewayNode + PubSubNode（LocalLink），持有全部 shard，不需要 etcd
ROLE=gateway  只有 GatewayNode；從 etcd watch shard map，WsLink 連各 pub/sub
ROLE=pubsub   只有 PubSubNode；向 etcd 註冊 + claim shard，/internal/ws 給 gateway 連
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import ADVERTISE_HTTP, ADVERTISE_WS, NODE_ID, NUM_SHARDS, ROLE, STATIC_DIR
from .errors import logger, setup_error_handling
from .shardmap import NodeInfo, ShardMap


def create_app() -> FastAPI:
    app = FastAPI(title=f"Messenger ({ROLE}:{NODE_ID})")
    setup_error_handling(app)
    info = NodeInfo(NODE_ID, ADVERTISE_WS, ADVERTISE_HTTP)
    state: dict = {}

    if ROLE == "all":
        from . import routes_gateway, routes_pubsub
        from .gateway import GatewayNode
        from .links import LocalLink
        from .pubsub import PubSubNode
        from .registry import static_cluster

        smap = static_cluster(info)
        node = PubSubNode(info, smap, member=None)
        gw_holder: list = []
        gw = GatewayNode(NODE_ID, smap, lambda nid, _url: LocalLink(nid, node, gw_holder[0]))
        gw_holder.append(gw)
        app.include_router(routes_gateway.build_router(gw, pubsub_node=node))
        app.include_router(routes_pubsub.build_router(node), prefix="/pubsub")

        @app.on_event("startup")
        async def _start_all():
            gw.start()
            logger.info("startup: role=all node=%s shards=%d (SQLite / memory registry)", NODE_ID, NUM_SHARDS)

        @app.on_event("shutdown")
        async def _stop_all():
            await gw.stop()

    elif ROLE == "gateway":
        from . import routes_gateway
        from .gateway import GatewayNode
        from .links import WsLink
        from .registry import ClusterView, etcd_client

        etcd = etcd_client()
        smap = ShardMap(num_shards=NUM_SHARDS)
        view = ClusterView(etcd, smap)
        holder: list = []
        gw = GatewayNode(NODE_ID, smap, lambda nid, url: WsLink(nid, url, holder[0]), view=view)
        holder.append(gw)
        app.include_router(routes_gateway.build_router(gw))

        @app.on_event("startup")
        async def _start_gw():
            view.start()
            gw.start()
            logger.info("startup: role=gateway node=%s etcd=%s", NODE_ID, etcd.url)

        @app.on_event("shutdown")
        async def _stop_gw():
            await gw.stop()
            await view.stop()

    elif ROLE == "pubsub":
        from . import routes_pubsub
        from .pubsub import PubSubNode
        from .registry import ClusterView, EtcdMember, etcd_client

        etcd = etcd_client()
        smap = ShardMap(num_shards=NUM_SHARDS)
        view = ClusterView(etcd, smap)
        node = PubSubNode(info, smap)
        member = EtcdMember(etcd, info, on_lease_lost=node.on_lease_lost)
        node.member = member
        app.include_router(routes_pubsub.build_router(node))
        state["node"] = node

        @app.on_event("startup")
        async def _start_ps():
            await member.start()
            view.start()
            node.start()
            logger.info("startup: role=pubsub node=%s advertise=%s etcd=%s", NODE_ID, ADVERTISE_WS, etcd.url)

        @app.on_event("shutdown")
        async def _stop_ps():
            await node.shutdown()  # SIGTERM → revoke lease → shard 立刻轉移
            await view.stop()
    else:
        raise RuntimeError(f"unknown ROLE={ROLE!r}")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
