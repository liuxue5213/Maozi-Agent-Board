#!/usr/bin/env python3
"""Probe IOReport (private-but-exported API) for GPU channel groups without sudo.
This is the approach used by Stats / pyIOReport to read GPU residency on macOS."""
import ctypes
import ctypes.util
import time

iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
libc = ctypes.CDLL(None)
libc.mach_task_self_.restype = ctypes.c_uint

CF = ctypes.c_void_p

cf.CFStringCreateWithCString.restype = CF
cf.CFStringCreateWithCString.argtypes = [CF, ctypes.c_char_p, ctypes.c_uint32]
cf.CFSetCreateMutable.restype = CF
cf.CFSetCreateMutable.argtypes = [CF, ctypes.c_long, CF]
cf.CFSetGetCount.restype = ctypes.c_long
cf.CFSetGetCount.argtypes = [CF]
cf.CFSetGetValues.restype = None
cf.CFSetGetValues.argtypes = [CF, ctypes.POINTER(CF)]
cf.CFDictionaryGetValue.restype = CF
cf.CFDictionaryGetValue.argtypes = [CF, CF]
cf.CFArrayGetCount.restype = ctypes.c_long
cf.CFArrayGetCount.argtypes = [CF]
cf.CFArrayGetValueAtIndex.restype = CF
cf.CFArrayGetValueAtIndex.argtypes = [CF, ctypes.c_long]
cf.CFStringGetCString.restype = ctypes.c_bool
cf.CFStringGetCString.argtypes = [CF, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
cf.CFGetTypeID.restype = ctypes.c_ulong
cf.CFGetTypeID.argtypes = [CF]
cf.CFStringGetTypeID.restype = ctypes.c_ulong
cf.CFArrayGetTypeID.restype = ctypes.c_ulong
cf.CFDictionaryGetTypeID.restype = ctypes.c_ulong
cf.CFNumberGetTypeID.restype = ctypes.c_ulong
cf.CFNumberGetValue.restype = ctypes.c_bool
cf.CFNumberGetValue.argtypes = [CF, ctypes.c_long, ctypes.POINTER(ctypes.c_double)]


def to_cf(s):
    return cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)


def cstr(cfstr):
    buf = ctypes.create_string_buffer(256)
    if cfstr and cf.CFStringGetCString(cfstr, buf, 256, 0x08000100):
        return buf.value.decode()
    return None


kcf_type_set_callbacks = ctypes.cast(cf.kCFTypeSetCallBacks, CF)

# candidate group names: GPU + a few known-good ones as sanity check
GROUPS = ["GPU Stats", "GPU Activity", "Intel GPU", "Graphics", "Energy Model",
          "CPU Stats", "CPU Core Power", "Thermal", "Disk"]

for gname in GROUPS:
    chans = cf.CFSetCreateMutable(None, 0, kcf_type_set_callbacks)
    r = iokit.IOReportCopyChannelsInGroup(to_cf(gname), chans, 0, 0, 0)
    n = cf.CFSetGetCount(chans)
    if r != 0:
        print(f"[{gname}] IOReportCopyChannelsInGroup error 0x{r:x}")
        continue
    if n == 0:
        print(f"[{gname}] no channels")
        continue
    print(f"[{gname}] {n} channel(s) found")

    # list channel names inside the group (elements are CFDictionary or CFString)
    buf = (CF * n)()
    cf.CFSetGetValues(chans, buf)
    shown = 0
    for i in range(n):
        el = buf[i]
        if not el:
            continue
        tid = cf.CFGetTypeID(el)
        if tid == cf.CFStringGetTypeID():
            if shown < 12:
                print(f"    - {cstr(el)}")
            shown += 1
        elif tid == cf.CFDictionaryGetTypeID():
            name = cf.CFDictionaryGetValue(el, to_cf("ChannelName")) or \
                   cf.CFDictionaryGetValue(el, to_cf("Name")) or \
                   cf.CFDictionaryGetValue(el, to_cf("kIOReportChannelName"))
            if name and shown < 12:
                print(f"    - {cstr(name)}")
                shown += 1
    if shown > 12:
        print(f"    ... and {shown - 12} more")

    # try a real sample: subscription -> sample -> sleep -> sample -> delta
    if gname not in ("GPU Stats", "GPU Activity", "Intel GPU"):
        continue
    try:
        sub_chans = CF()
        sub = iokit.IOReportCreateSubscription(libc.mach_task_self_(), chans,
                                               ctypes.byref(sub_chans), 0, None)
        if not sub:
            print(f"    [{gname}] subscription failed")
            continue
        s1 = iokit.IOReportCreateSamples(sub, sub_chans, None)
        time.sleep(1.0)
        s2 = iokit.IOReportCreateSamples(sub, sub_chans, None)
        delta = iokit.IOReportCreateSamplesDelta(s1, s2, None)
        cnt = cf.CFSetGetCount(delta)
        print(f"    [{gname}] sample delta groups: {cnt}")
        dbuf = (CF * max(cnt, 1))()
        if cnt:
            cf.CFSetGetValues(delta, dbuf)
        for i in range(min(cnt, 8)):
            el = dbuf[i]
            if not el:
                continue
            tid = cf.CFGetTypeID(el)
            if tid == cf.CFDictionaryGetTypeID():
                name = cf.CFDictionaryGetValue(el, to_cf("ChannelName"))
                states = cf.CFDictionaryGetValue(el, to_cf("States"))
                line = f"    ch: {cstr(name)}"
                if states and cf.CFGetTypeID(states) == cf.CFArrayGetTypeID():
                    m = cf.CFArrayGetCount(states)
                    parts = []
                    for j in range(min(m, 6)):
                        st = cf.CFArrayGetValueAtIndex(states, j)
                        if st and cf.CFGetTypeID(st) == cf.CFDictionaryGetTypeID():
                            sn = cstr(cf.CFDictionaryGetValue(st, to_cf("StateName")))
                            rv = cf.CFDictionaryGetValue(st, to_cf("Residency"))
                            val = None
                            if rv and cf.CFGetTypeID(rv) == cf.CFNumberGetTypeID():
                                d = ctypes.c_double()
                                cf.CFNumberGetValue(rv, 4, ctypes.byref(d))
                                val = d.value
                            parts.append(f"{sn}={val}")
                    line += "  states: " + ", ".join(parts)
                print(line)
    except Exception as e:
        print(f"    [{gname}] sampling failed: {e}")
