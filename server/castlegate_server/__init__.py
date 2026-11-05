def serverClassFactory(serverIface):
    from .plugin import CastlegateServer

    return CastlegateServer(serverIface)
