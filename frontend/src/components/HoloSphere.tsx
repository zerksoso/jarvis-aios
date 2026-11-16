import { useEffect, useRef } from 'react';
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  Group,
  LineBasicMaterial,
  LineLoop,
  Mesh,
  PerspectiveCamera,
  Points,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  WebGLRenderer,
} from 'three';
import { cn } from '@/lib/cn';
import type { EtatJarvis } from '@/lib/types';

export interface HoloSphereProps {
  etat: EtatJarvis;
  accentColor?: string;
  className?: string;
}

interface StatePreset {
  color: Color;
  rotSpeed: number;
  ringSpeed: number;
  pulseAmp: number;
  pulseFreq: number;
  noiseAmp: number;
  waveAmp: number;
  coreOpacity: number;
}

const PARTICLE_COUNT = 4000;
const SPHERE_RADIUS = 1.48;

const VERTEX_SHADER = /* glsl */ `
  uniform float uTime;
  uniform float uPulseAmp;
  uniform float uPulseFreq;
  uniform float uNoiseAmp;
  uniform float uWaveAmp;
  uniform float uSpeechAmp;
  uniform float uPixelRatio;

  attribute float aPhase;
  attribute float aScale;

  varying float vIntensity;

  float hashNoise(vec3 p) {
    return sin(p.x * 3.1 + uTime * 1.9) *
           cos(p.y * 3.4 - uTime * 1.5) *
           sin(p.z * 2.9 + uTime * 1.3);
  }

  void main() {
    vec3 dir = normalize(position);
    float pulse = sin(uTime * uPulseFreq + aPhase * 0.5) * uPulseAmp;
    float turb = hashNoise(dir * 2.2) * uNoiseAmp;
    float wave = sin(dir.y * 7.5 - uTime * 8.0 + aPhase) * uWaveAmp * (0.45 + uSpeechAmp);

    float radialOffset = pulse + turb + wave;
    vec3 displaced = position + dir * radialOffset;

    vec4 mvPosition = modelViewMatrix * vec4(displaced, 1.0);
    gl_Position = projectionMatrix * mvPosition;

    float distScale = 24.0 / max(1.0, -mvPosition.z);
    gl_PointSize = clamp(aScale * uPixelRatio * distScale, 1.2, 7.5);
    vIntensity = clamp(0.6 + radialOffset * 2.2 + uSpeechAmp * 0.35, 0.3, 1.35);
  }
`;

const FRAGMENT_SHADER = /* glsl */ `
  uniform vec3 uColor;
  varying float vIntensity;

  void main() {
    vec2 uv = gl_PointCoord - vec2(0.5);
    float d = length(uv);
    if (d > 0.5) discard;

    float alpha = smoothstep(0.5, 0.05, d) * 0.85 * vIntensity;
    vec3 finalColor = mix(uColor, vec3(0.95, 0.98, 1.0), smoothstep(0.28, 0.0, d) * 0.35);
    gl_FragColor = vec4(finalColor, alpha);
  }
`;

const CORE_VERTEX_SHADER = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vViewPos;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
    vViewPos = -mvPos.xyz;
    gl_Position = projectionMatrix * mvPos;
  }
`;

const CORE_FRAGMENT_SHADER = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec3 vNormal;
  varying vec3 vViewPos;

  void main() {
    vec3 viewDir = normalize(vViewPos);
    float fresnel = pow(1.0 - max(dot(vNormal, viewDir), 0.0), 2.2);
    float core = 0.22 + fresnel * 0.78;
    gl_FragColor = vec4(uColor, core * uOpacity);
  }
`;

function buildStatePreset(etat: EtatJarvis, accentHex: string): StatePreset {
  const baseAccent = new Color(accentHex || '#22d3ee');
  switch (etat) {
    case 'ecoute':
      return {
        color: baseAccent.clone().lerp(new Color('#38bdf8'), 0.35),
        rotSpeed: 0.24,
        ringSpeed: 0.55,
        pulseAmp: 0.11,
        pulseFreq: 4.4,
        noiseAmp: 0.02,
        waveAmp: 0.03,
        coreOpacity: 0.42,
      };
    case 'reflexion':
      return {
        color: new Color('#a78bfa'),
        rotSpeed: 0.34,
        ringSpeed: 0.85,
        pulseAmp: 0.04,
        pulseFreq: 2.5,
        noiseAmp: 0.16,
        waveAmp: 0.03,
        coreOpacity: 0.46,
      };
    case 'parole':
      return {
        color: baseAccent.clone().lerp(new Color('#2dd4bf'), 0.2),
        rotSpeed: 0.22,
        ringSpeed: 0.48,
        pulseAmp: 0.03,
        pulseFreq: 3.0,
        noiseAmp: 0.02,
        waveAmp: 0.14,
        coreOpacity: 0.48,
      };
    case 'action':
      return {
        color: new Color('#f59e0b'),
        rotSpeed: 0.42,
        ringSpeed: 1.55,
        pulseAmp: 0.07,
        pulseFreq: 5.5,
        noiseAmp: 0.05,
        waveAmp: 0.05,
        coreOpacity: 0.5,
      };
    case 'repos':
    default:
      return {
        color: baseAccent,
        rotSpeed: 0.12,
        ringSpeed: 0.25,
        pulseAmp: 0.03,
        pulseFreq: 1.6,
        noiseAmp: 0.015,
        waveAmp: 0.01,
        coreOpacity: 0.28,
      };
  }
}

/**
 * Sphère holographique WebGL (~4 000 particules Fibonacci, noyau lumineux et anneaux orbitaux).
 * Réagit aux 5 états de Jarvis avec interpolation douce des uniforms (~400 ms),
 * pause sur onglet caché, `devicePixelRatio` plafonné à 2 et nettoyage GPU complet.
 */
export function HoloSphere({
  etat,
  accentColor = '#22d3ee',
  className,
}: HoloSphereProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const targetRef = useRef<StatePreset>(buildStatePreset(etat, accentColor));

  useEffect(() => {
    targetRef.current = buildStatePreset(etat, accentColor);
  }, [etat, accentColor]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance',
      });
    } catch {
      return;
    }

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(dpr);
    renderer.setClearColor(0x000000, 0);

    const width = Math.max(container.clientWidth, 160);
    const height = Math.max(container.clientHeight, 160);
    renderer.setSize(width, height, false);
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';
    renderer.domElement.style.display = 'block';
    container.appendChild(renderer.domElement);

    const scene = new Scene();
    const camera = new PerspectiveCamera(42, width / height, 0.1, 50);
    camera.position.set(0, 0, 5.1);

    const rootGroup = new Group();
    scene.add(rootGroup);

    // 1. Nuage de ~4 000 particules en répartition de Fibonacci
    const positions = new Float32Array(PARTICLE_COUNT * 3);
    const phases = new Float32Array(PARTICLE_COUNT);
    const scales = new Float32Array(PARTICLE_COUNT);
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const y = 1 - (i / (PARTICLE_COUNT - 1)) * 2;
      const radiusAtY = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = goldenAngle * i;
      const x = Math.cos(theta) * radiusAtY;
      const z = Math.sin(theta) * radiusAtY;

      positions[i * 3] = x * SPHERE_RADIUS;
      positions[i * 3 + 1] = y * SPHERE_RADIUS;
      positions[i * 3 + 2] = z * SPHERE_RADIUS;
      phases[i] = (i % 97) * 0.065;
      scales[i] = 1.35 + ((i * 17) % 10) * 0.12;
    }

    const particlesGeo = new BufferGeometry();
    particlesGeo.setAttribute('position', new BufferAttribute(positions, 3));
    particlesGeo.setAttribute('aPhase', new BufferAttribute(phases, 1));
    particlesGeo.setAttribute('aScale', new BufferAttribute(scales, 1));

    const initial = targetRef.current;
    const particlesMat = new ShaderMaterial({
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
      uniforms: {
        uTime: { value: 0 },
        uColor: { value: initial.color.clone() },
        uPulseAmp: { value: initial.pulseAmp },
        uPulseFreq: { value: initial.pulseFreq },
        uNoiseAmp: { value: initial.noiseAmp },
        uWaveAmp: { value: initial.waveAmp },
        uSpeechAmp: { value: 0 },
        uPixelRatio: { value: dpr },
      },
    });

    const points = new Points(particlesGeo, particlesMat);
    rootGroup.add(points);

    // 2. Noyau lumineux interne
    const coreGeo = new SphereGeometry(0.62, 32, 32);
    const coreMat = new ShaderMaterial({
      vertexShader: CORE_VERTEX_SHADER,
      fragmentShader: CORE_FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
      uniforms: {
        uColor: { value: initial.color.clone() },
        uOpacity: { value: initial.coreOpacity },
      },
    });
    const coreMesh = new Mesh(coreGeo, coreMat);
    rootGroup.add(coreMesh);

    // 3. Anneaux orbitaux fins
    const ringGeometries: BufferGeometry[] = [];
    const ringMaterials: LineBasicMaterial[] = [];
    const rings: LineLoop[] = [];
    const ringSpecs = [
      { radius: 1.88, tiltX: Math.PI * 0.38, tiltZ: 0.22, opacity: 0.32 },
      { radius: 2.08, tiltX: -Math.PI * 0.28, tiltZ: -0.45, opacity: 0.2 },
      { radius: 2.26, tiltX: Math.PI * 0.12, tiltZ: 0.78, opacity: 0.14 },
    ];

    const ringSegments = 128;
    for (const spec of ringSpecs) {
      const ringPts = new Float32Array(ringSegments * 3);
      for (let i = 0; i < ringSegments; i++) {
        const angle = (i / ringSegments) * Math.PI * 2;
        ringPts[i * 3] = Math.cos(angle) * spec.radius;
        ringPts[i * 3 + 1] = 0;
        ringPts[i * 3 + 2] = Math.sin(angle) * spec.radius;
      }
      const rGeo = new BufferGeometry();
      rGeo.setAttribute('position', new BufferAttribute(ringPts, 3));
      const rMat = new LineBasicMaterial({
        color: initial.color.clone(),
        transparent: true,
        opacity: spec.opacity,
        blending: AdditiveBlending,
        depthWrite: false,
      });
      const ring = new LineLoop(rGeo, rMat);
      ring.rotation.x = spec.tiltX;
      ring.rotation.z = spec.tiltZ;
      rootGroup.add(ring);

      ringGeometries.push(rGeo);
      ringMaterials.push(rMat);
      rings.push(ring);
    }

    // ResizeObserver pour conserver un ratio net sans distorsion
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.max(entry.contentRect.width, 160);
        const h = Math.max(entry.contentRect.height, 160);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
      }
    });
    resizeObserver.observe(container);

    let rafId = 0;
    let lastTs = performance.now();
    let elapsed = 0;
    let isPaused = typeof document !== 'undefined' && document.hidden;

    const currentRotSpeed = { value: initial.rotSpeed };
    const currentRingSpeed = { value: initial.ringSpeed };

    const tick = (now: number) => {
      if (isPaused) return;
      const dt = Math.min((now - lastTs) / 1000, 0.05);
      lastTs = now;

      const motionScale = reducedMotion ? 0.18 : 1;
      elapsed += dt * motionScale;

      const target = targetRef.current;
      // Interpolation exponentielle ~400 ms (tau ≈ 0.13 s)
      const alpha = 1 - Math.exp(-dt / 0.13);

      (particlesMat.uniforms.uColor.value as Color).lerp(target.color, alpha);
      (coreMat.uniforms.uColor.value as Color).lerp(target.color, alpha);
      for (const rMat of ringMaterials) {
        rMat.color.lerp(target.color, alpha);
      }

      const u = particlesMat.uniforms;
      u.uTime.value = elapsed;
      u.uPulseAmp.value =
        (u.uPulseAmp.value as number) +
        (target.pulseAmp * motionScale - (u.uPulseAmp.value as number)) * alpha;
      u.uPulseFreq.value =
        (u.uPulseFreq.value as number) +
        (target.pulseFreq - (u.uPulseFreq.value as number)) * alpha;
      u.uNoiseAmp.value =
        (u.uNoiseAmp.value as number) +
        (target.noiseAmp * motionScale - (u.uNoiseAmp.value as number)) * alpha;
      u.uWaveAmp.value =
        (u.uWaveAmp.value as number) +
        (target.waveAmp * motionScale - (u.uWaveAmp.value as number)) * alpha;

      // Enveloppe vocale simulée multi-harmonique pendant la parole
      const speechTarget =
        target.waveAmp > 0.08
          ? 0.5 + 0.35 * Math.sin(elapsed * 9.5) + 0.15 * Math.cos(elapsed * 16.2)
          : 0;
      u.uSpeechAmp.value =
        (u.uSpeechAmp.value as number) +
        (speechTarget - (u.uSpeechAmp.value as number)) * alpha;

      coreMat.uniforms.uOpacity.value =
        (coreMat.uniforms.uOpacity.value as number) +
        (target.coreOpacity - (coreMat.uniforms.uOpacity.value as number)) * alpha;

      currentRotSpeed.value += (target.rotSpeed - currentRotSpeed.value) * alpha;
      currentRingSpeed.value += (target.ringSpeed - currentRingSpeed.value) * alpha;

      points.rotation.y += dt * currentRotSpeed.value * motionScale;
      points.rotation.x = Math.sin(elapsed * 0.25) * 0.08;

      if (rings[0]) rings[0].rotation.y += dt * currentRingSpeed.value * motionScale;
      if (rings[1]) rings[1].rotation.y -= dt * currentRingSpeed.value * 0.72 * motionScale;
      if (rings[2]) rings[2].rotation.z += dt * currentRingSpeed.value * 0.45 * motionScale;

      renderer.render(scene, camera);
      rafId = window.requestAnimationFrame(tick);
    };

    const startLoop = () => {
      if (rafId) window.cancelAnimationFrame(rafId);
      lastTs = performance.now();
      rafId = window.requestAnimationFrame(tick);
    };

    const handleVisibility = () => {
      if (document.hidden) {
        isPaused = true;
        if (rafId) {
          window.cancelAnimationFrame(rafId);
          rafId = 0;
        }
      } else {
        isPaused = false;
        startLoop();
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    if (!isPaused) {
      startLoop();
    } else {
      renderer.render(scene, camera);
    }

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      resizeObserver.disconnect();
      if (rafId) {
        window.cancelAnimationFrame(rafId);
      }
      particlesGeo.dispose();
      particlesMat.dispose();
      coreGeo.dispose();
      coreMat.dispose();
      for (const g of ringGeometries) g.dispose();
      for (const m of ringMaterials) m.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className={cn('relative h-full w-full select-none', className)}
      aria-label={`Sphère holographique Jarvis (${etat})`}
      role="img"
    />
  );
}
