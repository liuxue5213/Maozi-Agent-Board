#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
#import <dlfcn.h>
#import <math.h>

// MediaRemote's callback may outlive the caller. Keep it in this short-lived
// process so a framework failure cannot take down the WorkBuddy client.
int main(void) {
    @autoreleasepool {
        void *framework = dlopen(
            "/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote",
            RTLD_LAZY);
        if (!framework) return 2;
        typedef void (*get_info_t)(dispatch_queue_t, void (^)(CFDictionaryRef));
        get_info_t get_info = (get_info_t)dlsym(framework, "MRMediaRemoteGetNowPlayingInfo");
        if (!get_info) return 2;
        typedef void (*get_app_t)(dispatch_queue_t, void (^)(CFStringRef));
        get_app_t get_app = (get_app_t)dlsym(
            framework, "MRMediaRemoteGetNowPlayingApplicationDisplayID");
        typedef void (*get_playing_t)(dispatch_queue_t, void (^)(Boolean));
        get_playing_t get_playing = (get_playing_t)dlsym(
            framework, "MRMediaRemoteGetNowPlayingApplicationIsPlaying");

        NSString *(^string_value)(NSDictionary *, const char *) =
            ^NSString *(NSDictionary *info, const char *symbol) {
                NSString *const *key = (NSString *const *)dlsym(framework, symbol);
                id value = key ? info[*key] : nil;
                return [value isKindOfClass:[NSString class]] ? value : @"";
            };
        double (^number_value)(NSDictionary *, const char *) =
            ^double(NSDictionary *info, const char *symbol) {
                NSString *const *key = (NSString *const *)dlsym(framework, symbol);
                id value = key ? info[*key] : nil;
                return [value respondsToSelector:@selector(doubleValue)] ?
                    [value doubleValue] : 0;
            };

        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        dispatch_semaphore_t app_done = dispatch_semaphore_create(0);
        dispatch_semaphore_t playing_done = dispatch_semaphore_create(0);
        __block NSDictionary *track = nil;
        __block NSString *app_id = nil;
        __block BOOL is_playing = NO;
        get_info(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^(CFDictionaryRef raw) {
            NSDictionary *info = (__bridge NSDictionary *)raw;
            NSMutableDictionary *out = [NSMutableDictionary dictionary];
            if (info) {
                out[@"title"] = string_value(info, "kMRMediaRemoteNowPlayingInfoTitle");
                out[@"artist"] = string_value(info, "kMRMediaRemoteNowPlayingInfoArtist");
                out[@"album"] = string_value(info, "kMRMediaRemoteNowPlayingInfoAlbum");
                double duration = number_value(info, "kMRMediaRemoteNowPlayingInfoDuration");
                double rate = number_value(info, "kMRMediaRemoteNowPlayingInfoPlaybackRate");
                double position = number_value(info, "kMRMediaRemoteNowPlayingInfoElapsedTime");
                NSString *const *stamp_key = (NSString *const *)dlsym(
                    framework, "kMRMediaRemoteNowPlayingInfoTimestamp");
                id stamp = stamp_key ? info[*stamp_key] : nil;
                // MediaRemote's elapsed time is a base value at timestamp, not
                // a live counter. Without this the screen stays at 0:00.
                if (rate > 0 && [stamp isKindOfClass:[NSDate class]]) {
                    double age = -[(NSDate *)stamp timeIntervalSinceNow];
                    if (age > 0 && age < 86400) position += age * rate;
                }
                if (duration > 0) position = fmin(position, duration);
                out[@"position"] = @(fmax(0, position));
                out[@"duration"] = @(duration);
                out[@"playing"] = @(rate > 0);
            }
            track = out;
            dispatch_semaphore_signal(done);
        });
        if (get_app) {
            get_app(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^(CFStringRef raw) {
                app_id = (__bridge NSString *)raw;
                dispatch_semaphore_signal(app_done);
            });
        }
        if (get_playing) {
            get_playing(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^(Boolean value) {
                is_playing = value ? YES : NO;
                dispatch_semaphore_signal(playing_done);
            });
        }
        if (dispatch_semaphore_wait(done,
            dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC)) != 0) return 3;
        if (get_app) dispatch_semaphore_wait(app_done,
            dispatch_time(DISPATCH_TIME_NOW, 300 * NSEC_PER_MSEC));
        if (get_playing) dispatch_semaphore_wait(playing_done,
            dispatch_time(DISPATCH_TIME_NOW, 300 * NSEC_PER_MSEC));
        NSMutableDictionary *out = [track mutableCopy] ?: [NSMutableDictionary dictionary];
        if (app_id.length) out[@"bundle"] = app_id;
        if (get_playing && out[@"title"]) out[@"playing"] = @(is_playing);
        NSData *json = [NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
        if (!json) return 4;
        fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
        fflush(stdout);
        return 0;
    }
}
