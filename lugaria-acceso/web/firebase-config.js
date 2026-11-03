import { initializeApp } from "https://www.gstatic.com/firebasejs/10.8.0/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.8.0/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.8.0/firebase-firestore.js";

// Exportamos firebaseConfig para que common.js lo pueda importar
export const firebaseConfig = {
  apiKey: "AIzaSyB9b5kGcIAXltYazwKkrA3FW3Z3DwLMDI0",
  authDomain: "lugaria-guadalajara-9505e.firebaseapp.com",
  projectId: "lugaria-guadalajara-9505e",
  storageBucket: "lugaria-guadalajara-9505e.firebasestorage.app",
  messagingSenderId: "456373137134",
  appId: "1:456373137134:web:f7d150c367f3ebbdeb4dff",
  measurementId: "G-K1Z09169FH"
};

export const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db = getFirestore(app);