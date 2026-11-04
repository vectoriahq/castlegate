def serverClassFactory(serverIface):
    from .plugin import CastlegateSpike

    return CastlegateSpike(serverIface)
