/**
 * maozi-smcd — T2/Apple Silicon 特权 SMC 读取 helper（SMC_PRIVILEGED_PLAN 方案 B，M2）
 *
 * 由 launchd 以 root 常驻，监听本地 UNIX socket（/var/run/maozi-smc.sock，0600），
 * 只应答一种请求（行 JSON）：
 *   收：{"cmd":"read"}\n
 *   回：{"ok":1,"temp_c":57.25,"fan_rpm":2662}\n 或 {"ok":0,"err":"..."}\n
 *
 * 安全边界（计划 N1/N2/N4）：
 *   - 只读 SMC 键（TC0P/Tp09/Tp01 温度、F0Ac 风扇），绝不写任何键；
 *   - socket 0600 仅当前用户可连；数据不出网、不落盘日志；
 *   - 1s 内部缓存，杜绝高频 SMC 调用；
 *   - SIGTERM 时清理 socket 并 exit 0（launchd KeepAlive 可再拉起）。
 *
 * 编译：cc -O2 -Wall -framework IOKit -framework CoreFoundation -o maozi-smcd maozi-smcd.c
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <arpa/inet.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#define SOCKET_PATH "/var/run/maozi-smc.sock"
#define CACHE_SECS 1
#define RECV_BUF 512

static io_connect_t g_conn = 0;
static volatile sig_atomic_t g_stop = 0;

/* ---- SMC 底层（UInt32 版编码，与 Apple SMC 样例一致） ---- */
#define KERNEL_INDEX_SMC 2
#define SMC_CMD_READ_KEYINFO 9
#define SMC_CMD_READ_BYTES 5

typedef struct {
    uint8_t major, minor, build, revis;
    uint16_t release;
} SMCVersion;

typedef struct {
    uint16_t version, length;
    uint32_t cpuPLimit, gpuPLimit, memPLimit;
} SMCPLimitData;

typedef struct {
    uint32_t dataSize;
    uint32_t dataType;      /* FourCharCode */
    uint8_t dataAttributes;
} SMCKeyInfoData;

typedef struct {
    uint8_t key[4];
    SMCVersion vers;
    SMCPLimitData pLimitData;
    SMCKeyInfoData keyInfo;
    uint8_t result, status, data8;
    uint32_t data32;
    uint8_t bytes[32];
} SMCParamStruct;           /* 内核侧 80 字节（自然对齐），传 sizeof 即可 */

static SMCParamStruct smc_call(uint32_t selector, SMCParamStruct *in) {
    SMCParamStruct out;
    memset(&out, 0, sizeof(out));
    size_t in_size = sizeof(*in), out_size = sizeof(out);
    kern_return_t kr = IOConnectCallStructMethod(
        g_conn, selector, in, in_size, &out, &out_size);
    if (kr != KERN_SUCCESS) out.result = (uint8_t)(kr & 0xFF); /* 非 0 即错误 */
    return out;
}

/* 读一个键，按 dataType 解析成浮点；成功返回 0 */
static int smc_read_key(const char key[5], double *out_value) {
    SMCParamStruct in;
    memset(&in, 0, sizeof(in));
    memcpy(in.key, key, 4);
    in.data8 = SMC_CMD_READ_KEYINFO;
    SMCParamStruct info = smc_call(SMC_CMD_READ_KEYINFO, &in);
    if (info.result != 0) return (int)info.result;
    if (info.keyInfo.dataSize > 32) return 9001;

    memset(&in, 0, sizeof(in));
    memcpy(in.key, key, 4);
    in.keyInfo.dataSize = info.keyInfo.dataSize;
    in.data8 = SMC_CMD_READ_BYTES;
    SMCParamStruct data = smc_call(SMC_CMD_READ_BYTES, &in);
    if (data.result != 0) return (int)data.result;

    uint32_t type = info.keyInfo.dataSize >= 4 ? 0 : 0; /* dataType 判断见下 */
    type = info.keyInfo.dataType;
    if (info.keyInfo.dataSize == 4 && type == 'flts') {
        float f;
        memcpy(&f, data.bytes, 4);
        *out_value = f;
        return 0;
    }
    if (info.keyInfo.dataSize == 2 && (type == 'fp88' || type == 'sp78')) {
        *out_value = (data.bytes[0] * 256 + data.bytes[1]) / 256.0;
        return 0;
    }
    if (info.keyInfo.dataSize == 1 && type == 'ui8 ') {
        *out_value = data.bytes[0];
        return 0;
    }
    if (info.keyInfo.dataSize == 2 && type == 'ui16') {
        *out_value = data.bytes[0] * 256 + data.bytes[1];
        return 0;
    }
    return 9002;    /* 未知类型 */
}

/* 温度：按优先级试多个键，取第一个成功且在合理区间的值 */
static const char *TEMP_KEYS[] = {"TC0P", "TC0E", "TC0D", "Tp09", "Tp01"};
static int read_temp(double *out) {
    for (size_t i = 0; i < sizeof(TEMP_KEYS) / sizeof(TEMP_KEYS[0]); ++i) {
        double v;
        if (smc_read_key(TEMP_KEYS[i], &v) == 0 && v > -10 && v < 130) {
            *out = v;
            return 0;
        }
    }
    return -1;
}

static int read_fan(double *out) {
    double v;
    if (smc_read_key("F0Ac", &v) == 0 && v >= 0 && v < 20000) {
        *out = v;
        return 0;
    }
    return -1;
}

/* ---- socket 服务 ---- */
static void on_signal(int sig) { (void)sig; g_stop = 1; }

static int make_socket(void) {
    unlink(SOCKET_PATH);
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return -1; }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind"); close(fd); return -1;
    }
    chmod(SOCKET_PATH, 0600);           /* N1：仅当前用户可连 */
    if (listen(fd, 4) < 0) { perror("listen"); close(fd); return -1; }
    return fd;
}

int main(void) {
    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    signal(SIGPIPE, SIG_IGN);           /* 客户端断开不能带崩 helper */

    CFDictionaryRef matching = IOServiceMatching("AppleSMC");
#if defined(__MAC_12_0) && __MAC_OS_X_VERSION_MIN_REQUIRED >= __MAC_12_0
    io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault,
                                                       matching);
#else
    io_service_t service = IOServiceGetMatchingService(kIOMasterPortDefault,
                                                       matching);
#endif
    if (!service) { fprintf(stderr, "AppleSMC not found\n"); return 1; }
    kern_return_t kr = IOServiceOpen(service, mach_task_self(),
                                     KERNEL_INDEX_SMC, &g_conn);
    IOObjectRelease(service);
    if (kr != KERN_SUCCESS || !g_conn) {
        fprintf(stderr, "IOServiceOpen failed: %x\n", kr);
        return 1;
    }

    int listen_fd = make_socket();
    if (listen_fd < 0) return 1;

    /* 1s 缓存（N3）：后台连接频繁查询也不打 SMC */
    double cached_temp = -1000, cached_fan = -1;
    int temp_ok = 0, fan_ok = 0;
    time_t cache_at = 0;

    fprintf(stderr, "maozi-smcd ready on " SOCKET_PATH "\n");
    while (!g_stop) {
        int client = accept(listen_fd, NULL, NULL);
        if (client < 0) {
            if (errno == EINTR) continue;
            break;
        }
        char buf[RECV_BUF];
        ssize_t n = read(client, buf, sizeof(buf) - 1);
        if (n <= 0) { close(client); continue; }
        buf[n] = 0;
        /* 只认 {"cmd":"read"} 一种请求；其余一律拒绝（N1：无任意命令面） */
        char reply[256];
        if (!strstr(buf, "\"read\"")) {
            snprintf(reply, sizeof(reply), "{\"ok\":0,\"err\":\"bad request\"}");
        } else {
            time_t now = time(NULL);
            if (now - cache_at >= CACHE_SECS) {
                temp_ok = read_temp(&cached_temp);
                fan_ok = read_fan(&cached_fan);
                cache_at = now;
            }
            if (temp_ok == 0 || fan_ok == 0) {
                char t[32] = "null", f[32] = "null";
                if (temp_ok == 0) snprintf(t, sizeof(t), "%.2f", cached_temp);
                if (fan_ok == 0) snprintf(f, sizeof(f), "%.0f", cached_fan);
                snprintf(reply, sizeof(reply),
                         "{\"ok\":1,\"temp_c\":%s,\"fan_rpm\":%s}", t, f);
            } else {
                snprintf(reply, sizeof(reply),
                         "{\"ok\":0,\"err\":\"smc read failed\"}");
            }
        }
        strncat(reply, "\n", sizeof(reply) - strlen(reply) - 1);
        ssize_t wn = write(client, reply, strlen(reply));
        (void)wn;
        close(client);
    }

    close(listen_fd);
    unlink(SOCKET_PATH);
    IOServiceClose(g_conn);
    fprintf(stderr, "maozi-smcd stopped\n");
    return 0;
}
