// Black hole raymarching material (Three.js TSL, runs on WebGPU or WebGL2).
// Ported from MisterPrada/singularity (src/Experience/Worlds/MainWorld/BlackHole.js).
//
// The mesh is just a unit sphere used as a screen-space proxy. For every pixel it
// covers, this shader marches a ray through an imaginary volume:
//   * the ray direction is bent toward the centre by a 1/r^2 term (fake gravitational lensing)
//   * a thin slab around z = 0 is sampled against a rotating noise texture (the accretion disk)
//   * anything inside `originRadius` is opaque black (the shadow / event horizon)
//   * whatever light is left over samples the star map along the bent ray direction
import * as THREE from "three/webgpu";
import {
  Fn,
  Loop,
  abs,
  cameraPosition,
  color,
  equirectUV,
  faceDirection,
  float,
  max,
  mix,
  modelWorldMatrix,
  normalize,
  positionGeometry,
  positionWorld,
  remapClamp,
  step,
  sub,
  texture,
  time,
  uniform,
  vec3,
  vec4,
} from "three/tsl";
import {
  ColorRamp3_BSpline,
  lengthSqrt,
  linearToSrgb,
  rotateAxis,
  smoothRange,
  srgbToLinear,
  vecToFac,
  whiteNoise2D,
} from "./tslHelpers";

export function createBlackHoleUniforms() {
  return {
    /** Ray-march steps per pixel. Biggest performance knob. */
    iterations: uniform(float(128)),
    stepSize: uniform(float(0.0071)),
    /** Start-position jitter, hides banding. */
    noiseFactor: uniform(float(0.01)),
    /** Strength of the light bending. */
    power: uniform(float(0.3)),

    /** Radius of the black shadow. */
    originRadius: uniform(float(0.13)),
    /** Thickness of the accretion disk slab. */
    width: uniform(float(0.03)),

    rampCol1: uniform(color(0.95, 0.71, 0.44)),
    rampPos1: uniform(float(0.05)),
    rampCol2: uniform(color(0.14, 0.05, 0.03)),
    rampPos2: uniform(float(0.425)),
    rampCol3: uniform(color(0, 0, 0)),
    rampPos3: uniform(float(1.0)),

    rampEmission: uniform(float(2.0)),
    emissionColor: uniform(color(0.14, 0.129, 0.09)),
  };
}

export type BlackHoleUniforms = ReturnType<typeof createBlackHoleUniforms>;

interface Options {
  noiseTexture: THREE.Texture;
  starsTexture: THREE.Texture;
  /** Brightness multiplier of the star map seen through the lens. */
  backgroundIntensity?: number;
}

export function createBlackHoleMaterial({ noiseTexture, starsTexture, backgroundIntensity = 2 }: Options) {
  const uniforms = createBlackHoleUniforms();

  noiseTexture.wrapS = THREE.RepeatWrapping;
  noiseTexture.wrapT = THREE.RepeatWrapping;
  noiseTexture.needsUpdate = true;

  // No lights or environment in this scene, so a basic material that outputs the
  // colour node is equivalent to the original emissive-only standard material.
  const material = new THREE.MeshBasicNodeMaterial({ side: THREE.DoubleSide });

  material.colorNode = Fn(() => {
    // ==== Uniforms and constants ====
    const _step = uniforms.stepSize;
    const noiseAmp = uniforms.noiseFactor;
    const power = uniforms.power;
    const originRadius = uniforms.originRadius;
    const bandWidth = uniforms.width;
    const iterCount = uniforms.iterations;

    // ==== Geometry- and view-dependent bases ====
    const objCoords = positionGeometry.mul(vec3(1, 1, -1)).xzy; // flip Z then swizzle
    const isBackface = step(0.0, faceDirection.negate()); // 1 backface, 0 front

    // Camera as point in object space
    const camPointObj = cameraPosition.mul(modelWorldMatrix).mul(vec3(1, 1, -1)).xzy;

    // Pick coords from camera for backfaces, from geometry for frontfaces
    const startCoords = mix(objCoords, camPointObj.xyz, isBackface);

    // Incoming view direction in world, then to object-like swizzle
    const viewInWorld = normalize(sub(cameraPosition, positionWorld)).mul(vec3(1, 1, -1)).xzy;
    const rayDir = viewInWorld.negate(); // initial march direction

    // White noise to jitter start
    const noiseWhite = whiteNoise2D(objCoords.xy).mul(noiseAmp);
    const jitter = rayDir.mul(noiseWhite);

    // Ray initial position
    const rayPos = startCoords.sub(jitter);

    // Accumulators
    const colorAcc = vec3(0);
    const alphaAcc = float(0.0);

    // ==== Main loop ====
    Loop(iterCount, () => {
      // Steering term toward center
      const rNorm = normalize(rayPos);
      const rLen = lengthSqrt(rayPos);
      const steerMag = _step.mul(power).div(rLen.mul(rLen)); // step*power / r^2
      const range = remapClamp(rLen, 1.0, 0.5, 0.0, 1.0); // fade steering
      const steer = rNorm.mul(steerMag.mul(range));
      const steeredDir = rayDir.sub(steer).normalize();

      // Advance once
      const advance = rayDir.mul(_step);
      rayPos.addAssign(advance);

      // Local measures in XY plane and rotating UVs
      const xyLen = lengthSqrt(rayPos.mul(vec3(1, 1, 0)));
      const rotPhase = xyLen.mul(4.27).sub(time.mul(0.1));
      const uvAxis = vec3(0, 0, 1);
      const uvRot = rayPos.mul(rotateAxis(uvAxis, rotPhase));
      const uv = uvRot.mul(2);

      // Deep noise sample
      const noiseDeep = texture(noiseTexture, uv);

      // Z band shaping
      const bandMin = bandWidth.negate();
      const bandEnds = vec3(bandMin, 0.0, bandWidth); // [-w, 0, w]
      const dz = sub(bandEnds, vec3(rayPos.z));
      const zQuad = dz.mul(dz).div(bandWidth);
      const zBand = max(bandWidth.sub(zQuad).div(bandWidth), 0.0);

      // Modulated noise amplitude
      const noiseAmp3 = noiseDeep.mul(zBand);
      const noiseAmpLen = lengthSqrt(noiseAmp3);

      // Pseudo normal via offset noise
      const uvForNormal = uv.mul(1.002);
      const noiseNormal = texture(noiseTexture, uvForNormal).mul(zBand);
      const noiseNormalLen = lengthSqrt(noiseNormal);

      // Color ramp evaluation
      const rampInput = xyLen
        .add(noiseAmpLen.sub(0.78).mul(1.5))
        .add(noiseAmpLen.sub(noiseNormalLen).mul(19.75));

      const rampA = vec4(uniforms.rampCol1, uniforms.rampPos1);
      const rampB = vec4(uniforms.rampCol2, uniforms.rampPos2);
      const rampC = vec4(uniforms.rampCol3, uniforms.rampPos3);

      const baseCol = (ColorRamp3_BSpline as any)(rampInput.x, rampA, rampB, rampC);
      const emissiveCol = baseCol.mul(uniforms.rampEmission).add(uniforms.emissionColor);

      // Core suppression near origin
      const rLenNow = lengthSqrt(rayPos);
      const insideCore = rLenNow.lessThan(originRadius);
      const shadedCol = mix(emissiveCol, vec3(0), insideCore);

      // Alpha shaping
      const zAbs = abs(rayPos.z);
      const aNoise = noiseAmpLen.sub(0.75).mul(-0.6);
      const aPre = zAbs.add(aNoise);
      const aRadial = smoothRange(xyLen, 1.0, 0.0, 0.0, 1.0);
      const aBand = smoothRange(aPre, bandWidth, 0, 0, aRadial);
      const alphaLocal = mix(aBand, 1.0, insideCore);

      // Front-to-back compositing
      const oneMinusA = alphaAcc.oneMinus();
      const weight = oneMinusA.mul(vecToFac(alphaLocal));
      const newColor = mix(colorAcc, shadedCol, weight);
      const newAlpha = mix(alphaAcc, 1.0, vecToFac(alphaLocal));

      // Second advance and steering update
      rayPos.addAssign(advance);
      rayDir.assign(steeredDir);
      colorAcc.assign(newColor);
      alphaAcc.assign(newAlpha);
    });

    // ==== Environment blend on remaining transparency ====
    const dirForEnv = rayDir.mul(vec3(1, -1, 1)).xzy;
    const env = linearToSrgb(texture(starsTexture, equirectUV(dirForEnv)).mul(backgroundIntensity));

    const trans = float(1.0).sub(alphaAcc);
    const finalRGB = mix(colorAcc, env, trans.mul(1.0));

    return srgbToLinear(finalRGB);
  })();

  return { material, uniforms };
}
