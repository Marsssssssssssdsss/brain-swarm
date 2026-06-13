"""
全模块真实性验证脚本
"""
import sys, os, warnings
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
warnings.filterwarnings('ignore')
import numpy as np

errors = []

# 1. Config
try:
    from config import PipelineConfig, NeuromodConfig, NeuralSimConfig
    cfg = PipelineConfig()
    assert cfg.eeg.n_channels == 8
    assert cfg.eeg.sampling_rate == 250
    assert cfg.neural_sim.n_neurons == 32
    assert cfg.neuromod.method == 'tfus'
    print('[1/6] Config: PASS')
except Exception as e:
    errors.append(f'Config: {e}')
    print(f'[1/6] Config: FAIL - {e}')

# 2. Existing modules
try:
    from eeg_processor import EEGProcessor, EEGSimulator
    proc = EEGProcessor(n_channels=8, sampling_rate=250)
    proc.build_filters()
    sim = EEGSimulator(n_channels=8, sampling_rate=250, n_classes=6)
    data = sim.generate(0, 1.0)
    assert data.shape == (8, 250)
    clean = proc.preprocess(data)
    assert clean.shape == (8, 250)
    print('[2/6] EEG Processor + Simulator: PASS')
except Exception as e:
    errors.append(f'EEG Processor: {e}')
    print(f'[2/6] EEG Processor: FAIL - {e}')

# 3. Decoder
try:
    from brain_decoder import create_decoder, FBCSPDecoder
    from config import DecoderConfig
    dec_cfg = DecoderConfig()
    decoder = create_decoder(dec_cfg)
    n_classes = 6
    X = np.random.randn(60, 8, 250)
    y = np.array([i for i in range(6) for _ in range(10)])
    decoder.fit(X, y)
    pred, conf = decoder.predict(X[0])
    print(f'[3/6] Decoder (FBCSP+LDA): PASS (pred={pred}, conf={conf:.3f})')
except Exception as e:
    errors.append(f'Decoder: {e}')
    print(f'[3/6] Decoder: FAIL - {e}')

# 4. Action Mapper + Drone Controller
try:
    from action_mapper import ActionMapper
    from drone_controller import DroneSwarmController
    mapper = ActionMapper(action_file='src/preset_actions.json')
    actions = mapper.list_actions()
    assert len(actions) == 6
    swarm = DroneSwarmController(n_drones=3, simulation=True)
    cmd = mapper.get_action(0)
    result = swarm.execute(cmd)
    assert result is not None
    swarm.print_status()
    print(f'[4/6] Action Mapper + Drone Controller: PASS ({len(actions)} actions)')
except Exception as e:
    errors.append(f'Action/Drone: {e}')
    print(f'[4/6] Action/Drone: FAIL - {e}')

# 5. New modules
try:
    from neural_sim.spike_sim import SpikeSimulator, SpikeTrainConfig
    spike_cfg = SpikeTrainConfig(n_neurons=8, duration=0.5)
    spike_sim = SpikeSimulator(spike_cfg)
    train = spike_sim.generate_train()
    lfp = spike_sim.generate_lfp(train)
    assert train.shape[0] == 15000
    assert lfp.shape[0] == 15000
    print('[5/6] Neural Spike Sim: PASS')

    from neuromod.tfus import TFUSModulator, TFUSConfig
    tfus_cfg = TFUSConfig(frequency=500e3, intensity=1.0)
    mod = TFUSModulator(tfus_cfg)
    x = y = np.linspace(-5, 5, 50)
    pressure = mod.compute_pressure_field(x, y, z=20)
    assert pressure.shape == (50,)
    modulated = mod.simulate_stimulation(np.random.randn(250), 250)
    assert modulated.shape == (250,)
    update = mod.closed_loop_update(modulated, 'excite')
    assert update['new_intensity'] > update['previous_intensity']
    print('[5/6] tFUS Neuromodulation: PASS')

    from experience.demo_experience import InvasiveExperience
    exp = InvasiveExperience()
    for _ in range(100):
        exp.simulate_invasive_control(np.random.random())
        exp.measure_latency()
    score = exp.compute_immersion()
    assert 0 <= score <= 100
    report = exp.status_report()
    assert 'ms' in report
    print(f'[5/6] Experience Layer: PASS (immersion={score:.0f})')
except Exception as e:
    errors.append(f'New modules: {e}')
    print(f'[5/6] New modules: FAIL - {e}')

# 6. Closed-loop pipeline
try:
    from closed_loop_pipeline import ClosedLoopPipeline
    cfg = PipelineConfig()
    cfg.neural_sim.enable = True
    cfg.neuromod.enable = True
    pipeline = ClosedLoopPipeline(cfg)
    assert pipeline.spike_sim is not None
    assert pipeline.modulator is not None
    assert pipeline.experience is not None
    print('[6/6] ClosedLoopPipeline: PASS')
except Exception as e:
    errors.append(f'ClosedLoop: {e}')
    print(f'[6/6] ClosedLoopPipeline: FAIL - {e}')

# 7. Focus modules
try:
    from focus_detector import FocusDetector, BrainState
    fd = FocusDetector(sampling_rate=250)
    for i in range(200):
        fd.feed(np.random.randn(250) * 10)
        r = fd.get_report()
        if r:
            assert 0 <= r.focus <= 100
            assert 0 <= r.relaxation <= 100
            assert isinstance(r.state, BrainState)
            break
    print('[7/9] FocusDetector: PASS')
except Exception as e:
    errors.append(f'FocusDetector: {e}')
    print(f'[7/9] FocusDetector: FAIL - {e}')

try:
    from neuromod.tdcs import TDCSModulator, TDCSConfig
    tdcs = TDCSModulator(TDCSConfig(current_ma=1.0))
    tdcs.start()
    for _ in range(5):
        tdcs.step(dt=1.0)
    adj = tdcs.closed_loop_update(focus=80, relaxation=20)
    assert adj['current'] > 0
    tdcs.stop()
    print(f'[8/9] TDCSModulator: PASS')
except Exception as e:
    errors.append(f'TDCSModulator: {e}')
    print(f'[8/9] TDCSModulator: FAIL - {e}')

try:
    from closed_loop_pipeline import FocusLoopPipeline
    cfg = PipelineConfig()
    flp = FocusLoopPipeline(cfg)
    flp.run(duration=3.0, simulate=True)
    print('[9/9] FocusLoopPipeline: PASS')
except Exception as e:
    errors.append(f'FocusLoopPipeline: {e}')
    print(f'[9/9] FocusLoopPipeline: FAIL - {e}')

print()
print('=' * 50)
if errors:
    print(f'FAIL: {len(errors)} error(s):')
    for e in errors:
        print(f'  - {e}')
else:
    print('ALL 9/9 VERIFICATIONS PASSED')
print('=' * 50)
