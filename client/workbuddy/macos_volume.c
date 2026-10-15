#include <CoreAudio/CoreAudio.h>
#include <AudioToolbox/AudioHardwareService.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Read/write the current default output device, not a particular music app.
// JSON on stdout keeps the Python side small and avoids AppleScript's >1s startup.
static AudioObjectID output_device(void) {
    AudioObjectID device = kAudioObjectUnknown;
    AudioObjectPropertyAddress address = {
        kAudioHardwarePropertyDefaultOutputDevice,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = sizeof(device);
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &address,
            0, NULL, &size, &device) != noErr) return kAudioObjectUnknown;
    return device;
}

static int volume_property(AudioObjectID device, Float32 *value, int write_value) {
    AudioObjectPropertyAddress address = {
        kAudioHardwareServiceDeviceProperty_VirtualMainVolume,
        kAudioDevicePropertyScopeOutput,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = sizeof(*value);
    if (write_value)
        return AudioObjectSetPropertyData(device, &address, 0, NULL, size, value) == noErr;
    return AudioObjectGetPropertyData(device, &address, 0, NULL, &size, value) == noErr;
}

static int mute_property(AudioObjectID device, UInt32 *value, int write_value) {
    AudioObjectPropertyAddress address = {
        kAudioDevicePropertyMute,
        kAudioDevicePropertyScopeOutput,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = sizeof(*value);
    if (write_value)
        return AudioObjectSetPropertyData(device, &address, 0, NULL, size, value) == noErr;
    return AudioObjectGetPropertyData(device, &address, 0, NULL, &size, value) == noErr;
}

int main(int argc, char **argv) {
    AudioObjectID device = output_device();
    if (device == kAudioObjectUnknown) return 2;
    Float32 volume = 0;
    UInt32 muted = 0;
    if (!volume_property(device, &volume, 0)) return 3;
    int has_mute = mute_property(device, &muted, 0);

    if (argc == 3 && strcmp(argv[1], "step") == 0) {
        int delta = atoi(argv[2]);
        volume = fminf(1.0f, fmaxf(0.0f, volume + delta / 100.0f));
        if (!volume_property(device, &volume, 1)) return 4;
        if (delta > 0 && has_mute) {
            muted = 0;
            if (!mute_property(device, &muted, 1)) return 5;
        }
    } else if (argc == 2 && strcmp(argv[1], "toggle-mute") == 0) {
        if (!has_mute) return 6;
        muted = !muted;
        if (!mute_property(device, &muted, 1)) return 5;
    } else if (argc != 1) {
        return 7;
    }

    // HAL setters can be asynchronous; print a fresh read when available.
    volume_property(device, &volume, 0);
    if (has_mute) mute_property(device, &muted, 0);
    printf("{\"vol\":%d,\"mut\":%u}\n", (int)lroundf(volume * 100), muted);
    return 0;
}
