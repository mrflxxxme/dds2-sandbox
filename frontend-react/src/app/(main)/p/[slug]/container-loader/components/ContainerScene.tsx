'use client';

import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import type { ContainerSpec, PlacedBox, FreeSpace } from '../lib/packer';
import { BOX_COLORS } from '../lib/packer';

interface ContainerSceneProps {
    container: ContainerSpec;
    placedBoxes: PlacedBox[];
    freeSpaces: FreeSpace[];
    showFree: boolean;
}

export default function ContainerScene({ container, placedBoxes, freeSpaces, showFree }: ContainerSceneProps) {
    const mountRef = useRef<HTMLDivElement>(null);
    const frameRef = useRef<number | null>(null);
    const mouseRef = useRef({ isDown: false, startX: 0, startY: 0, rotX: 0.4, rotY: -0.6, dist: 1.8 });

    useEffect(() => {
        if (!mountRef.current) return;
        const mount = mountRef.current;

        const scene = new THREE.Scene();
        scene.background = new THREE.Color('#0d1117');

        const w = mount.clientWidth;
        const h = mount.clientHeight || 500;
        const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100);
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setSize(w, h);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.shadowMap.enabled = true;
        mount.appendChild(renderer.domElement);

        // Lighting
        scene.add(new THREE.AmbientLight(0xffffff, 0.5));
        const dl = new THREE.DirectionalLight(0xffffff, 0.8);
        dl.position.set(10, 15, 10);
        dl.castShadow = true;
        scene.add(dl);
        const dl2 = new THREE.DirectionalLight(0xffffff, 0.3);
        dl2.position.set(-5, 5, -5);
        scene.add(dl2);

        const maxDim = Math.max(container.l, container.w, container.h);
        const sc = 5 / maxDim;

        // Container wireframe
        const cg = new THREE.BoxGeometry(container.l * sc, container.h * sc, container.w * sc);
        const ce = new THREE.EdgesGeometry(cg);
        const cl = new THREE.LineSegments(ce, new THREE.LineBasicMaterial({ color: 0x6366f1 }));
        cl.position.set((container.l * sc) / 2, (container.h * sc) / 2, (container.w * sc) / 2);
        scene.add(cl);

        const cm = new THREE.Mesh(cg, new THREE.MeshPhysicalMaterial({
            color: 0x6366f1, transparent: true, opacity: 0.04,
            side: THREE.DoubleSide, depthWrite: false,
        }));
        cm.position.copy(cl.position);
        scene.add(cm);

        // Floor
        const fg = new THREE.PlaneGeometry(container.l * sc, container.w * sc);
        const fm = new THREE.Mesh(fg, new THREE.MeshStandardMaterial({ color: 0x1a1a1f, side: THREE.DoubleSide }));
        fm.rotation.x = -Math.PI / 2;
        fm.position.set((container.l * sc) / 2, 0.001, (container.w * sc) / 2);
        fm.receiveShadow = true;
        scene.add(fm);

        // Boxes
        placedBoxes.forEach(box => {
            const color = BOX_COLORS[box.typeIdx % BOX_COLORS.length];
            const g = new THREE.BoxGeometry(box.l * sc - 0.01, box.h * sc - 0.01, box.w * sc - 0.01);
            const mt = new THREE.MeshPhysicalMaterial({
                color: new THREE.Color(color),
                roughness: 0.3, metalness: 0.1, clearcoat: 0.3,
            });
            const ms = new THREE.Mesh(g, mt);
            ms.position.set(
                box.x * sc + (box.l * sc) / 2,
                box.y * sc + (box.h * sc) / 2,
                box.z * sc + (box.w * sc) / 2,
            );
            ms.castShadow = true;
            ms.receiveShadow = true;
            scene.add(ms);

            const eg = new THREE.EdgesGeometry(g);
            const ln = new THREE.LineSegments(eg, new THREE.LineBasicMaterial({
                color: 0x000000, opacity: 0.3, transparent: true,
            }));
            ln.position.copy(ms.position);
            scene.add(ln);
        });

        // Free spaces
        if (showFree && freeSpaces) {
            freeSpaces.forEach(sp => {
                if (sp.l * sp.w * sp.h < 0.005) return;
                const g = new THREE.BoxGeometry(sp.l * sc - 0.005, sp.h * sc - 0.005, sp.w * sc - 0.005);
                const mt = new THREE.MeshPhysicalMaterial({
                    color: 0xef4444, transparent: true, opacity: 0.1,
                    side: THREE.DoubleSide, depthWrite: false,
                });
                const ms = new THREE.Mesh(g, mt);
                ms.position.set(
                    sp.x * sc + (sp.l * sc) / 2,
                    sp.y * sc + (sp.h * sc) / 2,
                    sp.z * sc + (sp.w * sc) / 2,
                );
                scene.add(ms);

                const eg = new THREE.EdgesGeometry(g);
                const lm = new THREE.LineDashedMaterial({
                    color: 0xef4444, dashSize: 0.08, gapSize: 0.04,
                    opacity: 0.6, transparent: true,
                });
                const ln = new THREE.LineSegments(eg, lm);
                ln.position.copy(ms.position);
                ln.computeLineDistances();
                scene.add(ln);
            });
        }

        // Camera orbit
        const cx = (container.l * sc) / 2;
        const cy = (container.h * sc) / 2;
        const cz = (container.w * sc) / 2;
        const m = mouseRef.current;

        function updCam() {
            const d = m.dist * maxDim * sc;
            camera.position.set(
                cx + d * Math.sin(m.rotY) * Math.cos(m.rotX),
                cy + d * Math.sin(m.rotX),
                cz + d * Math.cos(m.rotY) * Math.cos(m.rotX),
            );
            camera.lookAt(cx, cy, cz);
        }
        updCam();

        // Mouse events
        const canvas = renderer.domElement;
        const md = (e: MouseEvent) => { m.isDown = true; m.startX = e.clientX; m.startY = e.clientY; };
        const mm = (e: MouseEvent) => {
            if (!m.isDown) return;
            m.rotY -= (e.clientX - m.startX) * 0.005;
            m.rotX = Math.max(-1.47, Math.min(1.47, m.rotX + (e.clientY - m.startY) * 0.005));
            m.startX = e.clientX; m.startY = e.clientY;
            updCam();
        };
        const mu = () => { m.isDown = false; };
        const mw = (e: WheelEvent) => { m.dist = Math.max(0.5, Math.min(5, m.dist + e.deltaY * 0.001)); updCam(); };

        // Touch events
        const ts = (e: TouchEvent) => {
            if (e.touches.length === 1) {
                m.isDown = true; m.startX = e.touches[0].clientX; m.startY = e.touches[0].clientY;
            }
        };
        const tmv = (e: TouchEvent) => {
            e.preventDefault();
            if (e.touches.length === 1 && m.isDown) {
                m.rotY -= (e.touches[0].clientX - m.startX) * 0.005;
                m.rotX = Math.max(-1.47, Math.min(1.47, m.rotX + (e.touches[0].clientY - m.startY) * 0.005));
                m.startX = e.touches[0].clientX; m.startY = e.touches[0].clientY;
                updCam();
            }
        };
        const te = () => { m.isDown = false; };

        canvas.addEventListener('mousedown', md);
        canvas.addEventListener('mousemove', mm);
        canvas.addEventListener('mouseup', mu);
        canvas.addEventListener('mouseleave', mu);
        canvas.addEventListener('wheel', mw);
        canvas.addEventListener('touchstart', ts);
        canvas.addEventListener('touchmove', tmv, { passive: false });
        canvas.addEventListener('touchend', te);

        // Animation loop
        function anim() {
            frameRef.current = requestAnimationFrame(anim);
            renderer.render(scene, camera);
        }
        anim();

        // Resize
        const onResize = () => {
            const nw = mount.clientWidth;
            const nh = mount.clientHeight || 500;
            camera.aspect = nw / nh;
            camera.updateProjectionMatrix();
            renderer.setSize(nw, nh);
        };
        window.addEventListener('resize', onResize);

        return () => {
            window.removeEventListener('resize', onResize);
            canvas.removeEventListener('mousedown', md);
            canvas.removeEventListener('mousemove', mm);
            canvas.removeEventListener('mouseup', mu);
            canvas.removeEventListener('mouseleave', mu);
            canvas.removeEventListener('wheel', mw);
            canvas.removeEventListener('touchstart', ts);
            canvas.removeEventListener('touchmove', tmv);
            canvas.removeEventListener('touchend', te);
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
            renderer.dispose();
            if (mount.contains(renderer.domElement)) {
                mount.removeChild(renderer.domElement);
            }
        };
    }, [container, placedBoxes, freeSpaces, showFree]);

    return <div ref={mountRef} className="scene-container" />;
}
