import time
import numpy as np
import pyroomacoustics as pra
import matplotlib.pyplot as plt
import sounddevice as sd
from scipy.io import wavfile

import piva

# We use several sound samples for each source to have a long enough length
wav_files = [
    [
        "examples/input_samples/cmu_arctic_us_aew_a0001.wav",
        "examples/input_samples/cmu_arctic_us_aew_a0002.wav",
        "examples/input_samples/cmu_arctic_us_aew_a0003.wav",
    ],
    [
        "examples/input_samples/cmu_arctic_us_axb_a0004.wav",
        "examples/input_samples/cmu_arctic_us_axb_a0005.wav",
        "examples/input_samples/cmu_arctic_us_axb_a0006.wav",
    ],
]


def create_mix(filename=None):

    # Room 4m by 6m
    room_dim = [8, 9]

    # create an anechoic room with sources and mics
    room = pra.ShoeBox(room_dim, fs=16000, max_order=0)

    # get signals
    signals = [
        np.concatenate([wavfile.read(f)[1].astype(np.float32) for f in source_files])
        for source_files in wav_files
    ]
    delays = [1.0, 0.0]
    locations = [[2.5, 3], [2.5, 6]]

    # add mic and good source to room
    # Add silent signals to all sources
    for sig, d, loc in zip(signals, delays, locations):
        room.add_source(loc, signal=sig, delay=d)

    # add microphone array
    room.add_microphone_array(
        pra.MicrophoneArray(np.c_[[6.5, 4.47], [6.5, 4.53], [6.5, 4.59]], fs=room.fs)
    )

    # compute RIRs
    room.compute_rir()

    def callback_mix(premix):

        sigma_s = np.std(premix[:, premix.shape[1] // 2, :], axis=1)
        premix /= sigma_s[:, None, None]
        premix[1:] *= 0.01  # make secondary sources quieter

        mix = np.sum(premix, axis=0)

        scale = np.maximum(np.max(np.abs(premix)), np.max(np.abs(mix)))

        mix *= 0.95 / scale
        premix *= 0.95 / scale

        return mix

    # Run the simulation
    separate_recordings = room.simulate(callback_mix=callback_mix, return_premix=True)
    mics_signals = room.mic_array.signals

    if filename is not None:
        wavfile.write(filename, room.fs, (mics_signals.T * (2 ** 15)).astype(np.int16))

    return mics_signals, separate_recordings, len(locations)


if __name__ == "__main__":

    mix, references, n_src = create_mix()

    # STFT parameters
    framesize = 512
    hop = framesize // 2
    win_a = pra.hann(framesize)
    win_s = pra.transform.compute_synthesis_window(win_a, framesize // 2)

    # shape == (n_chan, n_frames, n_freq)
    X = pra.transform.analysis(mix.T, framesize, hop, win=win_a)
    n_frames, n_freq, n_chan = X.shape

    n_repeat = 100
    n_iter = 3

    results = {}

    for model in ["gauss", "laplace"]:

        results[model] = {}

        for backend in ["py", "cpp"]:

            # Run algorithm
            t1 = time.perf_counter()
            for i in range(n_repeat):
                Y, W = piva.five(
                    X,
                    n_iter=n_iter,
                    model=model,
                    proj_back=True,
                    return_filters=True,
                    backend=backend,
                )
            t2 = time.perf_counter()

            y = pra.transform.synthesis(Y[:, :, 0], framesize, hop, win=win_s)
            y = y[framesize - hop :]

            # Separation error
            m = np.minimum(y.shape[0], references.shape[2])
            error = np.mean((references[0, 0, :m] - y[:m]) ** 2)

            runtime = (t2 - t1) / n_repeat

            # store the results
            results[model][backend] = {
                "runtime": runtime,
                "error": error,
                "output": y.copy(),
            }

            print(
                f"model={model} backend={backend} error={error} runtime={runtime:.6f}"
            )

        # compute difference between python and cpp implementations
        backend_diff = np.max(
            np.abs(results[model]["py"]["output"] - results[model]["cpp"]["output"])
        )
        results[model]["backend_diff"] = backend_diff
        print(f"model={model} backend_diff={backend_diff}")
